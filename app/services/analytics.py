"""Required KPIs (§8.1)."""

from typing import Optional
from sqlalchemy.orm import Session

from app.models import (
    Business,
    Message,
    Opportunity,
    Qualification,
    SourceRecord,
    Suppression,
    WebsiteBuild,
)


def _ratio(numerator: int, denominator: int) -> Optional[float]:
    return round(numerator / denominator, 3) if denominator else None


def kpi_snapshot(db: Session) -> dict:
    raw_records = db.query(SourceRecord).count()
    unique_businesses = db.query(Business).count()
    verified = db.query(Business).filter(Business.state != "new").count()

    latest_quals: dict[int, Qualification] = {}
    for q in db.query(Qualification).order_by(Qualification.created_at.asc()).all():
        latest_quals[q.business_id] = q
    shortlist = [q for q in latest_quals.values() if q.routing == "priority_review"]
    approved_states = {
        "shortlisted", "preview_ready", "revision", "approved", "contacted",
        "engaged", "follow_up", "sql", "meeting", "won",
    }
    human_approved = db.query(Business).filter(Business.state.in_(approved_states)).count()

    builds = db.query(WebsiteBuild).all()
    deployed = [b for b in builds if b.status in ("deployed", "expired")]

    sent = db.query(Message).filter(Message.status == "sent", Message.direction == "out").count()
    opt_outs = db.query(Suppression).filter(Suppression.reason == "opt_out").count()

    sqls = db.query(Business).filter(Business.state.in_(["sql", "meeting", "won"])).count()
    meetings = db.query(Opportunity).filter(Opportunity.stage.in_(["meeting", "proposal", "won"])).count()
    wins = db.query(Opportunity).filter(Opportunity.stage == "won").count()

    return {
        "funnel": {
            "raw_records": raw_records,
            "unique_businesses": unique_businesses,
            "verified": verified,
            "qualified": len(shortlist),
            "human_approved": human_approved,
        },
        "source_to_valid": _ratio(unique_businesses, raw_records),
        "shortlist_precision": _ratio(human_approved, len(shortlist)),  # target >= 0.8
        "asset_completion": _ratio(len(deployed), human_approved),
        "sites": {
            "total_builds": len(builds),
            "deployed": len(deployed),
            "failed": sum(1 for b in builds if b.status == "failed"),
        },
        "outreach": {
            "sent": sent,
            "opt_outs": opt_outs,
            "opt_out_rate": _ratio(opt_outs, sent),
        },
        "sales": {
            "sql": sqls,
            "meetings": meetings,
            "wins": wins,
        },
    }
