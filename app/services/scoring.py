"""FitQL scoring against the versioned rubric (§4.3, FITQL LOCK 30/25/20/15/10).

Exposes both raw 0–100 dimension scores and a weighted breakdown plus
human-readable reasons so reviewers understand Tier A decisions.
"""

from typing import Optional
from sqlalchemy.orm import Session

from app.models import Business, ContactPoint, Evidence, Qualification, WebsiteAudit
from app.services.config_loader import load


# Public breakdown keys (weighted points that sum toward total)
BREAKDOWN_KEYS = {
    "digital_gap": "digital_gap",
    "icp_fit": "commercial_fit",
    "value_capacity": "website_quality",  # demand/capacity signal; labelled for UI clarity
    "contactability": "contactability",
    "evidence_quality": "data_confidence",
}


def compute_dimension_scores(db: Session, business: Business, rubric: dict) -> dict[str, float]:
    """Deterministic 0-100 per-dimension scores from stored evidence."""
    scores: dict[str, float] = {}

    icp = rubric.get("icp", {})
    icp_score = 0.0
    if business.category in icp.get("categories", []):
        icp_score += 60
    if business.city in icp.get("cities", []):
        icp_score += 40
    scores["icp_fit"] = icp_score

    audit = (
        db.query(WebsiteAudit)
        .filter(WebsiteAudit.business_id == business.id)
        .order_by(WebsiteAudit.created_at.desc())
        .first()
    )
    if audit is None:
        scores["digital_gap"] = 0.0
    else:
        gap = 0.0
        if not audit.has_website:
            gap = 100.0
        else:
            if not audit.mobile_ok:
                gap += 30
            if not audit.has_booking_cta:
                gap += 40
            if not audit.services_clear:
                gap += 15
            if not audit.contact_clear:
                gap += 15
        scores["digital_gap"] = min(gap, 100.0)

    evidence = (
        db.query(Evidence)
        .filter(Evidence.business_id == business.id, Evidence.status != "missing")
        .all()
    )
    demand_keys = {"rating", "review_count", "service_breadth", "practitioners", "locations"}
    demand_found = sum(1 for e in evidence if e.fact_key in demand_keys)
    scores["value_capacity"] = min(demand_found / len(demand_keys), 1.0) * 100

    contacts = db.query(ContactPoint).filter(ContactPoint.business_id == business.id).all()
    verified_business = [c for c in contacts if c.verified and c.is_business_contact]
    if verified_business:
        scores["contactability"] = 100.0
    elif any(c.is_business_contact for c in contacts):
        scores["contactability"] = 50.0
    else:
        scores["contactability"] = 0.0

    if evidence:
        avg_conf = sum(e.confidence for e in evidence) / len(evidence)
        scores["evidence_quality"] = avg_conf * 100
    else:
        scores["evidence_quality"] = 0.0

    return scores


def build_reasons(db: Session, business: Business, dim_scores: dict[str, float]) -> list[str]:
    reasons: list[str] = []
    audit = (
        db.query(WebsiteAudit)
        .filter(WebsiteAudit.business_id == business.id)
        .order_by(WebsiteAudit.created_at.desc())
        .first()
    )
    contacts = db.query(ContactPoint).filter(ContactPoint.business_id == business.id).all()

    if audit:
        if not audit.has_website:
            reasons.append("No website")
        else:
            if not audit.has_booking_cta:
                reasons.append("No booking flow")
            if not audit.mobile_ok:
                reasons.append("Outdated website")
            if not audit.services_clear:
                reasons.append("Services unclear")
        if audit.notes and "Instagram" in audit.notes:
            reasons.append("Active Instagram")

    if any(c.channel == "instagram" and c.value for c in contacts):
        if "Active Instagram" not in reasons:
            reasons.append("Active Instagram")
    if any(c.channel == "phone" and c.verified for c in contacts):
        reasons.append("Verified phone")
    if any(c.channel == "email" and c.verified for c in contacts):
        reasons.append("Verified email")

    if dim_scores.get("icp_fit", 0) >= 100:
        reasons.append("ICP match (category + city)")
    elif dim_scores.get("icp_fit", 0) >= 60:
        reasons.append("Partial ICP match")

    # Deduplicate preserving order
    seen = set()
    ordered = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            ordered.append(r)
    return ordered


def score_business(db: Session, business: Business, rubric_version: Optional[str] = None) -> Qualification:
    rubric = load("scoring", rubric_version)
    weights: dict[str, float] = rubric["weights"]
    thresholds = rubric["thresholds"]

    dim_scores = compute_dimension_scores(db, business, rubric)
    total = sum(dim_scores.get(dim, 0.0) * weight / 100 for dim, weight in weights.items())

    breakdown = {
        BREAKDOWN_KEYS.get(dim, dim): round(dim_scores.get(dim, 0.0) * weight / 100, 2)
        for dim, weight in weights.items()
    }
    # Also keep raw-weighted aliases matching the product brief
    breakdown["total"] = round(total, 2)

    if total >= thresholds["priority_review"]:
        routing = "priority_review"
    elif total >= thresholds["manual_nurture"]:
        routing = "manual_nurture"
    else:
        routing = "reject"

    reasons = build_reasons(db, business, dim_scores)

    qualification = Qualification(
        business_id=business.id,
        rubric_version=rubric["_version"],
        scores=dim_scores,
        breakdown=breakdown,
        reasons=reasons,
        total=round(total, 2),
        routing=routing,
    )
    db.add(qualification)
    db.flush()
    return qualification


def assign_tiers(db: Session, rubric_version: Optional[str] = None) -> dict[str, list[int]]:
    """Tier A = highest-fit 15, Tier B = next 15 among priority_review (FITQL LOCK)."""
    rubric = load("scoring", rubric_version)
    tier_a_size = rubric.get("tiers", {}).get("a_size", 15)
    tier_b_size = rubric.get("tiers", {}).get("b_size", 15)

    latest_per_business: dict[int, Qualification] = {}
    for q in db.query(Qualification).order_by(Qualification.created_at.asc()).all():
        latest_per_business[q.business_id] = q

    eligible = sorted(
        (q for q in latest_per_business.values() if q.routing == "priority_review"),
        key=lambda q: q.total,
        reverse=True,
    )
    result = {"A": [], "B": []}
    for idx, q in enumerate(eligible):
        if idx < tier_a_size:
            q.tier = "A"
            result["A"].append(q.business_id)
        elif idx < tier_a_size + tier_b_size:
            q.tier = "B"
            result["B"].append(q.business_id)
        else:
            q.tier = None
        db.add(q)
    db.flush()
    return result
