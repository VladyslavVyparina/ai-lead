"""Operator UI tests: cookie auth and the full pilot loop through HTML forms."""

import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.main import app


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
        with TestClient(app, follow_redirects=False) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def _login(client):
    response = client.post("/ui/login", data={"token": settings.admin_token})
    assert response.status_code == 303
    assert response.headers["location"] == "/ui"
    cookie = response.headers["set-cookie"]
    assert settings.admin_token not in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie


def test_ui_requires_login(client):
    for path in ("/ui", "/ui/sources", "/ui/leads", "/ui/outreach"):
        response = client.get(path)
        assert response.status_code == 303
        assert response.headers["location"] == "/ui/login"


def test_login_rejects_wrong_token(client):
    response = client.post("/ui/login", data={"token": "wrong"})
    assert response.status_code == 303
    assert "err=" in response.headers["location"]
    assert client.get("/ui").status_code == 303  # still not authed


def test_dashboard_renders_after_login(client):
    _login(client)
    response = client.get("/ui")
    assert response.status_code == 200
    assert "Дашборд" in response.text
    assert "AI Lead" in response.text
    assert "Skylix" not in response.text


def test_quick_add_and_csv_import_via_ui(client):
    _login(client)
    client.post(
        "/ui/sources/connectors",
        data={"name": "ui-manual", "adapter": "csv_import", "permitted_use": "pilot"},
    )

    added = client.post(
        "/ui/sources/quick-add",
        data={
            "connector_id": 1,
            "business_name": "UI Spa",
            "category": "massage_wellness",
            "city": "porto",
            "profile_url": "https://maps.example/ui-spa",
            "email": "ui@spa.pt",
        },
    )
    assert added.status_code == 303
    assert "/ui/leads/1" in added.headers["location"]

    csv_content = "name,category,city,address,website,email,phone\nCSV Spa,beauty_aesthetics,braga,,,csv@spa.pt,\n"
    upload = client.post(
        "/ui/sources/1/import-csv",
        files={"file": ("leads.csv", io.BytesIO(csv_content.encode()), "text/csv")},
    )
    assert upload.status_code == 303

    leads_page = client.get("/ui/leads")
    assert "UI Spa" in leads_page.text
    assert "CSV Spa" in leads_page.text


def test_full_operator_flow_via_ui(client):
    _login(client)
    client.post(
        "/ui/sources/connectors",
        data={"name": "ui-flow", "adapter": "csv_import", "permitted_use": "pilot"},
    )
    client.post(
        "/ui/sources/quick-add",
        data={
            "connector_id": 1,
            "business_name": "Flow UI Spa",
            "category": "massage_wellness",
            "city": "porto",
            "phone": "+351911999888",
            "email": "flowui@spa.pt",
        },
    )
    business_id = 1

    # Verify both imported contacts (ids 1 and 2).
    for contact_id in (1, 2):
        client.post(f"/ui/leads/{business_id}/contacts/{contact_id}/verify")

    client.post(f"/ui/leads/{business_id}/audit", data={"notes": "no site found"})
    for key in ("rating", "review_count", "service_breadth", "practitioners", "locations"):
        client.post(
            f"/ui/leads/{business_id}/evidence",
            data={"fact_key": key, "fact_value": "5", "source_url": "https://src", "confidence": "1.0"},
        )
    client.post(
        f"/ui/leads/{business_id}/evidence",
        data={"fact_key": "address", "fact_value": "Rua UI 1", "source_url": "https://src", "confidence": "1.0"},
    )

    score = client.post(f"/ui/leads/{business_id}/score")
    assert score.status_code == 303
    assert "priority_review" in score.headers["location"]

    client.post(f"/ui/leads/{business_id}/transition", data={"target": "shortlisted"})
    preview = client.post(f"/ui/leads/{business_id}/build-preview")
    assert "err=" not in preview.headers["location"]

    proposal = client.post(f"/ui/leads/{business_id}/proposal", data={"offer_package": "local_pro"})
    assert "err=" not in proposal.headers["location"]

    client.post(f"/ui/leads/{business_id}/transition", data={"target": "approved"})
    draft = client.post(
        "/ui/outreach/draft",
        data={"business_id": business_id, "channel": "email", "touch_number": 1, "body": "Olá!"},
    )
    assert "err=" not in draft.headers["location"]

    client.post("/ui/outreach/1/approve")
    sent = client.post("/ui/outreach/1/mark-sent")
    assert "err=" not in sent.headers["location"]

    detail = client.get(f"/ui/leads/{business_id}")
    assert "contacted" in detail.text
    assert "https://preview-" in detail.text

    # Suppression blocks the next approval.
    client.post(f"/ui/leads/{business_id}/suppress", data={"reason": "opt_out"})
    client.post(
        "/ui/outreach/draft",
        data={"business_id": business_id, "channel": "email", "touch_number": 2, "body": "De novo"},
    )
    blocked = client.post("/ui/outreach/2/approve")
    assert "err=" in blocked.headers["location"]
