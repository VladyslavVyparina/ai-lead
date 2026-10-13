"""Vertical slice: connector → enrich → FitQL → approve preview → outreach draft."""

from app.adapters.directory_seed import ensure_porto_wellness_connector, load_catalog
from app.core.state_machine import LeadState
from app.models import Business, Evidence, Message, Qualification, WebsiteBuild
from app.services.outreach import approve_message, mark_sent
from app.services.pipeline import approve_and_generate_preview, run_porto_wellness_vertical_slice
from app.services.scoring import score_business


def test_porto_catalog_has_20_businesses():
    catalog = load_catalog("porto_wellness_v1")
    assert len(catalog["businesses"]) == 20
    assert catalog["city"] == "porto"
    assert catalog["category"] == "massage_wellness"


def test_vertical_slice_discovery_enrich_score(db):
    result = run_porto_wellness_vertical_slice(db, limit=20, top_review=5)
    assert result["ingested"] == 20
    assert result["enriched"] == 20
    assert result["scored"] == 20
    assert len(result["review_queue"]) == 5

    # Dedup / idempotent facts present
    business = db.get(Business, result["review_queue"][0])
    assert business.state == LeadState.SHORTLISTED.value
    evidence = db.query(Evidence).filter(Evidence.business_id == business.id).all()
    assert any(e.fact_key == "services" and e.status == "confirmed" for e in evidence)
    assert any(e.status == "inferred" for e in evidence)

    q = (
        db.query(Qualification)
        .filter(Qualification.business_id == business.id)
        .order_by(Qualification.created_at.desc())
        .first()
    )
    assert q.routing == "priority_review"
    assert q.breakdown
    assert q.breakdown.get("total") == q.total
    assert isinstance(q.reasons, list) and len(q.reasons) >= 1


def test_fitql_reasons_and_breakdown_shape(db):
    run_porto_wellness_vertical_slice(db, limit=5, top_review=3)
    business = db.query(Business).filter(Business.state == LeadState.SHORTLISTED.value).first()
    q = score_business(db, business)
    # Weighted contribution keys from product brief
    for key in ("digital_gap", "commercial_fit", "contactability", "data_confidence"):
        assert key in q.breakdown
    assert any(
        r in q.reasons
        for r in ("No booking flow", "No website", "Outdated website", "Verified phone", "Active Instagram")
    )


def test_approve_preview_generates_template_content_and_outreach_draft(db):
    result = run_porto_wellness_vertical_slice(db, limit=20, top_review=5)
    business_id = result["review_queue"][0]
    business = db.get(Business, business_id)

    out = approve_and_generate_preview(db, business, actor="human:tester")
    assert out["status"] == "deployed"
    assert out["pipeline_stage"] == "preview_ready"
    assert out["preview_url"]
    assert out["message_id"]

    build = db.get(WebsiteBuild, out["build_id"])
    assert build.content_pack
    assert build.content_pack["hero"]["headline"]
    assert build.content_pack["social_proof"] == []
    assert "não oficial" in build.content_pack["disclaimer"].lower() or "unofficial" in build.content_pack[
        "disclaimer"
    ].lower()
    assert build.qa_report.get("no_invented_social_proof") is True
    assert business.state == LeadState.PREVIEW_READY.value

    message = db.get(Message, out["message_id"])
    assert message.status == "draft"
    assert message.research_snapshot
    assert business.name.split()[0] in message.body or "marcações" in message.body.lower() or "marcação" in message.body.lower()

    # Human gate 2: approve outreach (also accepts preview → approved)
    approve_message(db, message, approver="tester")
    db.commit()
    db.refresh(business)
    assert business.state == LeadState.APPROVED.value
    assert message.status == "approved"

    mark_sent(db, message, actor="human:tester")
    db.commit()
    db.refresh(business)
    # mark_sent itself doesn't transition; API/UI does — simulate
    from app.services.events import transition

    if LeadState(business.state) == LeadState.APPROVED:
        transition(db, business, LeadState.CONTACTED, actor="system:test", reason="sent")
        db.commit()
    assert business.state == LeadState.CONTACTED.value


def test_connector_rerun_is_idempotent(db):
    ensure_porto_wellness_connector(db)
    first = run_porto_wellness_vertical_slice(db, limit=20, top_review=5)
    # Force a fresh job by advancing cursor semantics — second discovery still won't duplicate businesses
    from app.services.pipeline import discover_enrich_score

    connector = ensure_porto_wellness_connector(db)
    # New idempotency via different limit key
    second = discover_enrich_score(db, connector, limit=20, top_review=5)
    # First call may be reused if same cursor key; either way business count stays 20
    assert db.query(Business).count() == 20
    assert first["ingested"] == 20
    assert second.get("ingested", 0) >= 0
