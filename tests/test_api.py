"""API-level integration tests: auth, webhook, CSV upload, end-to-end pilot flow."""

import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.services.preview_forms import form_token

ADMIN = {"x-admin-token": settings.admin_token}
WEBHOOK = {"x-webhook-secret": settings.inbound_webhook_secret}


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def _csv_file(rows: list[str]):
    content = "name,category,city,address,website,email,phone\n" + "\n".join(rows) + "\n"
    return {"file": ("leads.csv", io.BytesIO(content.encode()), "text/csv")}


def _create_connector(client, name="api-csv", adapter="csv_import") -> int:
    response = client.post(
        "/connectors",
        headers=ADMIN,
        json={"name": name, "adapter": adapter, "owner": "op", "permitted_use": "pilot"},
    )
    assert response.status_code == 200
    return response.json()["id"]


# --- Auth ----------------------------------------------------------------------


def test_admin_endpoints_reject_bad_token(client):
    assert client.get("/connectors").status_code == 401
    assert client.get("/connectors", headers={"x-admin-token": "wrong"}).status_code == 401
    assert client.get("/analytics/kpi").status_code == 401


def test_webhook_rejects_bad_secret(client):
    payload = {"campaign": "c", "business_name": "B", "idempotency_key": "k1"}
    assert client.post("/webhooks/inbound", json=payload).status_code == 401
    assert (
        client.post("/webhooks/inbound", headers={"x-webhook-secret": "wrong"}, json=payload).status_code
        == 401
    )


def test_health_is_public(client):
    assert client.get("/health").json() == {"ok": True}


# --- Ingestion via HTTP ---------------------------------------------------------


def test_duplicate_connector_name_conflict(client):
    _create_connector(client, name="dup")
    response = client.post(
        "/connectors",
        headers=ADMIN,
        json={"name": "dup", "adapter": "csv_import", "owner": "op", "permitted_use": "pilot"},
    )
    assert response.status_code == 409


def test_csv_import_and_pagination(client):
    connector_id = _create_connector(client)
    rows = [f"API Spa {i},massage_wellness,porto,,,api{i}@x.pt," for i in range(7)]
    response = client.post(f"/connectors/{connector_id}/import-csv", headers=ADMIN, files=_csv_file(rows))
    assert response.status_code == 200
    assert response.json()["ingested"] == 7

    page = client.get("/businesses", headers=ADMIN, params={"limit": 3, "offset": 3}).json()
    assert page["total"] == 7
    assert len(page["items"]) == 3
    assert page["items"][0]["name"] == "API Spa 3"


def test_csv_import_rejects_non_utf8(client):
    connector_id = _create_connector(client)
    bad = {"file": ("leads.csv", io.BytesIO(b"\xff\xfe\x00broken"), "text/csv")}
    response = client.post(f"/connectors/{connector_id}/import-csv", headers=ADMIN, files=bad)
    assert response.status_code == 400


def test_inbound_webhook_creates_mql_idempotently(client):
    _create_connector(client, name="inbound", adapter="inbound_webhook")
    payload = {
        "campaign": "site-form",
        "business_name": "Webhook Spa",
        "email": "hook@spa.pt",
        "city": "porto",
        "idempotency_key": "wh-1",
    }
    first = client.post("/webhooks/inbound", headers=WEBHOOK, json=payload)
    assert first.status_code == 200
    assert first.json()["created"] is True
    assert first.json()["state"] == "mql"

    replay = client.post("/webhooks/inbound", headers=WEBHOOK, json=payload)
    assert replay.json()["created"] is False
    assert replay.json()["business_id"] == first.json()["business_id"]


# --- End-to-end pilot flow -------------------------------------------------------


def test_full_pilot_flow(client):
    """CSV → evidence/audit → score → shortlist → preview → proposal → outreach → suppression."""
    connector_id = _create_connector(client)
    rows = ["Flow Spa,massage_wellness,porto,,,flow@spa.pt,+351911222333"]
    client.post(f"/connectors/{connector_id}/import-csv", headers=ADMIN, files=_csv_file(rows))
    business_id = client.get("/businesses", headers=ADMIN).json()["items"][0]["id"]

    # Verify imported contacts, otherwise low-confidence facts block the preview build.
    detail = client.get(f"/businesses/{business_id}", headers=ADMIN).json()
    assert all(not c["verified"] for c in detail["contacts"])
    contacts = client.get(f"/businesses/{business_id}", headers=ADMIN).json()["contacts"]
    for contact in contacts:
        r = client.post(
            f"/businesses/{business_id}/contacts/{contact['id']}/verify", headers=ADMIN
        )
        assert r.status_code == 200

    client.post(f"/businesses/{business_id}/audit", headers=ADMIN, json={"has_website": False})
    client.post(
        f"/businesses/{business_id}/evidence",
        headers=ADMIN,
        json=[
            {"fact_key": k, "fact_value": 5, "source_url": "https://src", "confidence": 1.0}
            for k in ("rating", "review_count", "service_breadth", "practitioners", "locations")
        ]
        + [
            {"fact_key": "address", "fact_value": "Rua F 1", "source_url": "https://src", "confidence": 1.0},
            {
                "fact_key": "services",
                "fact_value": [{"name": "Massagem", "price": "EUR 40"}],
                "source_url": "https://src",
                "confidence": 1.0,
            },
        ],
    )

    score = client.post(f"/businesses/{business_id}/score", headers=ADMIN).json()
    assert score["routing"] == "priority_review"
    assert score["state"] == "fitql"

    r = client.post(
        f"/businesses/{business_id}/transition",
        headers=ADMIN,
        json={"target": "shortlisted", "actor": "human:op"},
    )
    assert r.status_code == 200

    preview = client.post(f"/businesses/{business_id}/build-preview", headers=ADMIN).json()
    assert preview["status"] == "deployed"
    assert preview["preview_url"].startswith("https://preview-")
    form_response = client.post(
        "/preview-form",
        json={
            "build_id": preview["build_id"],
            "form_token": form_token(preview["build_id"]),
            "name": "Maria",
            "contact": "+351911",
            "message": "Sábado 15h",
        },
    )
    assert form_response.status_code == 200
    client.post("/ui/login", data={"token": settings.admin_token})
    dashboard = client.get("/ui")
    assert "Maria" in dashboard.text
    assert "+351911" in dashboard.text

    proposal = client.post(
        f"/businesses/{business_id}/proposal", headers=ADMIN, json={"offer_package": "local_pro"}
    ).json()
    assert proposal["content"]["offer"]["price_from_eur"] == 500

    client.post(
        f"/businesses/{business_id}/transition", headers=ADMIN, json={"target": "approved", "actor": "human:op"}
    )
    draft = client.post(
        f"/businesses/{business_id}/messages",
        headers=ADMIN,
        json={"channel": "email", "touch_number": 1, "body": "Olá", "idempotency_key": "flow-m1"},
    ).json()
    client.post(f"/messages/{draft['message_id']}/approve", headers=ADMIN, json={"approver": "op"})
    sent = client.post(f"/messages/{draft['message_id']}/mark-sent", headers=ADMIN)
    assert sent.status_code == 200

    state = client.get(f"/businesses/{business_id}", headers=ADMIN).json()["state"]
    assert state == "contacted"

    client.post(f"/businesses/{business_id}/suppress", headers=ADMIN, json={"reason": "opt_out"})
    blocked = client.post(
        f"/businesses/{business_id}/messages",
        headers=ADMIN,
        json={"channel": "email", "touch_number": 2, "body": "Olá de novo", "idempotency_key": "flow-m2"},
    )
    approve_blocked = client.post(
        f"/messages/{blocked.json()['message_id']}/approve", headers=ADMIN, json={"approver": "op"}
    )
    assert approve_blocked.status_code == 409

    kpi = client.get("/analytics/kpi", headers=ADMIN).json()
    assert kpi["outreach"]["sent"] == 1
    assert kpi["outreach"]["opt_outs"] == 1


def test_preview_form_rejects_unknown_build(client):
    response = client.post(
        "/preview-form",
        json={
            "build_id": 999,
            "form_token": form_token(999),
            "name": "Maria",
            "contact": "+351911",
            "message": "Sábado 15h",
        },
    )
    assert response.status_code == 404


def test_preview_form_cors_preflight(client):
    response = client.options(
        "/preview-form",
        headers={
            "Origin": "https://preview-example.vercel.app",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://preview-example.vercel.app"


def test_preview_form_cors_allows_127_and_posts_to_dashboard(client):
    """Regression: browsers often use 127.0.0.1, not localhost."""
    from app.services.preview_forms import form_token

    preflight = client.options(
        "/preview-form",
        headers={
            "Origin": "http://127.0.0.1:3100",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://127.0.0.1:3100"

    connector_id = _create_connector(client, name="form-dash")
    client.post(
        f"/connectors/{connector_id}/import-csv",
        headers=ADMIN,
        files=_csv_file(["Dash Spa,massage_wellness,porto,,,dash@spa.pt,+351911000333"]),
    )
    business_id = client.get("/businesses", headers=ADMIN).json()["items"][0]["id"]
    # Verify contacts + evidence enough to build
    detail = client.get(f"/businesses/{business_id}", headers=ADMIN).json()
    for contact in detail["contacts"]:
        client.post(f"/businesses/{business_id}/contacts/{contact['id']}/verify", headers=ADMIN)
    client.post(f"/businesses/{business_id}/audit", headers=ADMIN, json={"has_website": False})
    client.post(
        f"/businesses/{business_id}/evidence",
        headers=ADMIN,
        json=[
            {"fact_key": k, "fact_value": 5, "confidence": 1.0}
            for k in ("rating", "review_count", "service_breadth", "practitioners", "locations")
        ]
        + [{"fact_key": "address", "fact_value": "Rua D 1", "confidence": 1.0}],
    )
    client.post(f"/businesses/{business_id}/score", headers=ADMIN)
    client.post(
        f"/businesses/{business_id}/transition",
        headers=ADMIN,
        json={"target": "shortlisted", "actor": "human:op"},
    )
    preview = client.post(f"/businesses/{business_id}/build-preview", headers=ADMIN).json()
    assert preview["status"] == "deployed"

    posted = client.post(
        "/preview-form",
        headers={"Origin": "http://127.0.0.1:3100"},
        json={
            "build_id": preview["build_id"],
            "form_token": form_token(preview["build_id"]),
            "name": "CORS User",
            "contact": "cors@test.pt",
            "message": "Quero marcar",
        },
    )
    assert posted.status_code == 200, posted.text

    client.post("/ui/login", data={"token": settings.admin_token})
    dashboard = client.get("/ui")
    assert "CORS User" in dashboard.text
    assert "cors@test.pt" in dashboard.text


def test_invalid_transition_returns_409(client):
    connector_id = _create_connector(client)
    client.post(
        f"/connectors/{connector_id}/import-csv",
        headers=ADMIN,
        files=_csv_file(["T Spa,massage_wellness,porto,,,t@spa.pt,"]),
    )
    business_id = client.get("/businesses", headers=ADMIN).json()["items"][0]["id"]
    response = client.post(
        f"/businesses/{business_id}/transition",
        headers=ADMIN,
        json={"target": "contacted", "actor": "human:op"},
    )
    assert response.status_code == 409


def test_config_endpoints_and_rollback_guard(client):
    config = client.get("/config/scoring", headers=ADMIN).json()
    assert config["active"] == "v1"
    assert config["config"]["weights"]["digital_gap"] == 30

    missing = client.post("/config/scoring/activate", headers=ADMIN, json={"version": "v99"})
    assert missing.status_code == 404
