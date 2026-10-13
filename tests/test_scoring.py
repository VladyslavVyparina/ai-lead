from app.core.schemas import Provenance, SourceRecordIn
from app.models import ContactPoint, Evidence, WebsiteAudit
from app.services.ingestion import ingest_record
from app.services.scoring import assign_tiers, score_business


def _business(db, connector, name, city="porto", category="massage_wellness"):
    record = SourceRecordIn(
        business_name=name,
        category=category,
        city=city,
        provenance=Provenance(source="test", permitted_use="test"),
    )
    business, _ = ingest_record(db, connector, record)
    return business


def _enrich_strong(db, business):
    db.add(WebsiteAudit(business_id=business.id, has_website=False))
    db.add(ContactPoint(business_id=business.id, channel="email", value=f"b{business.id}@x.pt", verified=True))
    for key in ("rating", "review_count", "service_breadth", "practitioners", "locations"):
        db.add(Evidence(business_id=business.id, fact_key=key, fact_value=5, confidence=1.0))
    db.flush()


def test_strong_fit_routes_to_priority_review(db, connector):
    business = _business(db, connector, "Top Spa")
    _enrich_strong(db, business)
    q = score_business(db, business)
    db.commit()
    assert q.routing == "priority_review"
    assert q.total >= 75
    assert q.rubric_version == "v1"
    assert q.breakdown["total"] == q.total
    assert q.reasons


def test_wrong_city_and_no_evidence_rejects(db, connector):
    business = _business(db, connector, "Far Away Spa", city="madrid", category="restaurant")
    q = score_business(db, business)
    db.commit()
    assert q.routing == "reject"
    assert q.total < 55


def test_weights_follow_locked_rubric(db, connector):
    business = _business(db, connector, "Weights Spa")
    _enrich_strong(db, business)
    q = score_business(db, business)
    # digital_gap 30, icp 25, value 20, contactability 15, evidence 10
    expected = (
        q.scores["digital_gap"] * 0.30
        + q.scores["icp_fit"] * 0.25
        + q.scores["value_capacity"] * 0.20
        + q.scores["contactability"] * 0.15
        + q.scores["evidence_quality"] * 0.10
    )
    assert abs(q.total - expected) < 0.01


def test_tier_assignment_top15_a_next15_b(db, connector):
    for i in range(40):
        business = _business(db, connector, f"Spa {i}")
        _enrich_strong(db, business)
        score_business(db, business)
    db.commit()
    result = assign_tiers(db)
    db.commit()
    assert len(result["A"]) == 15
    assert len(result["B"]) == 15
