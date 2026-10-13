"""Enrichment: website/social-style facts → evidence with status labels.

For the pilot vertical slice, enrichment reads the seed catalog snapshot
attached on the SourceRecord (and/or reloads by business name). Live HTTP
fetch adapters plug into `fetch_website_signals` later; facts are never invented.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.adapters.directory_seed import load_catalog
from app.models import Business, ContactPoint, Evidence, SourceRecord, WebsiteAudit
from app.services import dedupe
from app.services.events import record_event

FACT_STATUS = ("confirmed", "inferred", "missing", "conflicting", "stale")


def _upsert_contact(
    db: Session,
    business: Business,
    channel: str,
    value: str,
    *,
    verified: bool = False,
) -> None:
    value = (value or "").strip()
    if not value:
        return
    if channel == "email":
        value = value.lower()
    if channel == "instagram" and value.startswith("@"):
        value = value  # keep handle form
    normalized = dedupe.normalize_contact(channel, value)
    existing = (
        db.query(ContactPoint)
        .filter(
            ContactPoint.business_id == business.id,
            ContactPoint.channel == channel,
            ContactPoint.normalized_value == normalized,
        )
        .first()
    )
    if existing:
        if verified and not existing.verified:
            existing.verified = True
            db.add(existing)
        return
    db.add(
        ContactPoint(
            business_id=business.id,
            channel=channel,
            value=value,
            normalized_value=normalized,
            verified=verified,
            is_business_contact=True,
        )
    )


def _store_fact(
    db: Session,
    business: Business,
    key: str,
    value,
    *,
    source_url: str = "",
    confidence: float = 1.0,
    status: str = "confirmed",
) -> None:
    existing = (
        db.query(Evidence)
        .filter(Evidence.business_id == business.id, Evidence.fact_key == key)
        .order_by(Evidence.captured_at.desc())
        .first()
    )
    if existing and existing.fact_value == value and existing.status == status:
        return
    if existing and existing.fact_value != value and existing.status == "confirmed" and status == "confirmed":
        status = "conflicting"
    db.add(
        Evidence(
            business_id=business.id,
            fact_key=key,
            fact_value=value,
            source_url=source_url,
            confidence=confidence,
            status=status,
        )
    )


def _seed_for_business(db: Session, business: Business) -> Optional[dict]:
    """Recover enrichment payload from the latest source record extra.seed."""
    record = (
        db.query(SourceRecord)
        .filter(SourceRecord.business_id == business.id)
        .order_by(SourceRecord.created_at.desc())
        .first()
    )
    if record and isinstance(record.raw, dict):
        extra = record.raw.get("extra") or {}
        seed = extra.get("seed")
        if isinstance(seed, dict):
            return seed
    # Fallback: match by name in default catalog
    try:
        catalog = load_catalog("porto_wellness_v1")
    except FileNotFoundError:
        return None
    for entry in catalog.get("businesses") or []:
        if entry.get("name", "").strip().lower() == business.name.strip().lower():
            return entry
    return None


def enrich_business(db: Session, business: Business, *, actor: str = "system:enrichment") -> dict:
    """Extract contacts/services/hours/audit and store evidence with status."""
    seed = _seed_for_business(db, business)
    source_url = (seed or {}).get("website_url") or (seed or {}).get("profile_url") or business.website_url or ""

    if seed:
        if seed.get("website_url") and not business.website_url:
            business.website_url = seed["website_url"]
        if seed.get("profile_url") and not business.profile_url:
            business.profile_url = seed["profile_url"]
        if seed.get("address") and not business.address:
            business.address = seed["address"]
        db.add(business)

        # Contacts from directory snapshot — phone/email treated as confirmed public listings
        _upsert_contact(db, business, "phone", seed.get("phone", ""), verified=bool(seed.get("phone")))
        _upsert_contact(db, business, "email", seed.get("email", ""), verified=bool(seed.get("email")))
        _upsert_contact(db, business, "instagram", seed.get("instagram", ""), verified=False)

        if seed.get("address"):
            _store_fact(db, business, "address", seed["address"], source_url=source_url, status="confirmed")
        if seed.get("hours"):
            _store_fact(db, business, "hours", seed["hours"], source_url=source_url, status="confirmed")
        if seed.get("services"):
            _store_fact(db, business, "services", seed["services"], source_url=source_url, confidence=0.95, status="confirmed")
        if seed.get("rating") is not None:
            _store_fact(db, business, "rating", seed["rating"], source_url=source_url, status="confirmed")
        if seed.get("review_count") is not None:
            _store_fact(db, business, "review_count", seed["review_count"], source_url=source_url, status="confirmed")

        service_count = len(seed.get("services") or [])
        _store_fact(
            db,
            business,
            "service_breadth",
            service_count,
            source_url=source_url,
            status="inferred",
            confidence=0.8,
        )
        _store_fact(db, business, "practitioners", 1 if service_count else 0, source_url=source_url, status="inferred", confidence=0.6)
        _store_fact(db, business, "locations", 1, source_url=source_url, status="inferred", confidence=0.7)

        if seed.get("notes"):
            _store_fact(db, business, "research_notes", seed["notes"], source_url=source_url, status="inferred", confidence=0.7)

        description = f"{business.name} — massagem e bem-estar em {business.city.title()}."
        _store_fact(db, business, "description", description, source_url=source_url, status="inferred", confidence=0.75)

        db.add(
            WebsiteAudit(
                business_id=business.id,
                has_website=bool(seed.get("has_website")),
                mobile_ok=bool(seed.get("mobile_ok")),
                has_booking_cta=bool(seed.get("has_booking_cta")),
                services_clear=bool(seed.get("services_clear")),
                contact_clear=bool(seed.get("contact_clear")),
                notes=seed.get("notes", ""),
            )
        )
    else:
        # Mark key commercial fields as missing so UI/scoring can show gaps
        for key in ("hours", "services", "description"):
            has = (
                db.query(Evidence)
                .filter(Evidence.business_id == business.id, Evidence.fact_key == key)
                .first()
            )
            if not has:
                _store_fact(db, business, key, None, status="missing", confidence=0.0)
        db.add(
            WebsiteAudit(
                business_id=business.id,
                has_website=bool(business.website_url),
                mobile_ok=False,
                has_booking_cta=False,
                services_clear=False,
                contact_clear=bool(
                    db.query(ContactPoint).filter(ContactPoint.business_id == business.id).first()
                ),
                notes="enrichment without seed snapshot",
            )
        )

    summary = fact_status_summary(db, business)
    record_event(
        db,
        action="business_enriched",
        actor=actor,
        business_id=business.id,
        entity="business",
        entity_id=business.id,
        payload=summary,
        idempotency_key=f"enriched:{business.id}:{business.updated_at.isoformat() if business.updated_at else business.id}",
    )
    db.flush()
    return summary


def fact_status_summary(db: Session, business: Business) -> dict:
    evidence = db.query(Evidence).filter(Evidence.business_id == business.id).all()
    by_status: dict[str, list[str]] = {s: [] for s in FACT_STATUS}
    for e in evidence:
        status = e.status if e.status in by_status else "inferred"
        by_status[status].append(e.fact_key)
    return {
        "confirmed": by_status["confirmed"],
        "inferred": by_status["inferred"],
        "missing": by_status["missing"],
        "conflicting": by_status["conflicting"],
        "stale": by_status["stale"],
        "fact_count": len(evidence),
    }
