"""Source-aware ingestion (§3.1): normalize → dedupe → persist with provenance.

The same code path serves discovered (CSV/manual/directory) and inbound
(webhook) records — no source-specific workflow forks.
"""

from typing import Optional
import hashlib
import json

from sqlalchemy.orm import Session

from app.core.schemas import InboundLeadIn, Provenance, SourceRecordIn
from app.core.state_machine import LeadState
from app.models import Business, ContactPoint, SourceConnector, SourceRecord
from app.services import dedupe
from app.services.events import record_event, transition


def _idempotency_key(connector_id: int, record: SourceRecordIn) -> str:
    payload = json.dumps(
        [connector_id, record.business_name, record.city, record.email, record.phone],
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _upsert_contact(db: Session, business: Business, channel: str, value: str) -> None:
    value = value.strip()
    if not value:
        return
    if channel == "email":
        value = value.lower()
    normalized = dedupe.normalize_contact(channel, value)
    exists = (
        db.query(ContactPoint)
        .filter(
            ContactPoint.business_id == business.id,
            ContactPoint.channel == channel,
            ContactPoint.normalized_value == normalized,
        )
        .first()
    )
    if not exists:
        db.add(
            ContactPoint(
                business_id=business.id,
                channel=channel,
                value=value,
                normalized_value=normalized,
            )
        )


def ingest_record(
    db: Session,
    connector: SourceConnector,
    record: SourceRecordIn,
    *,
    route: str = "discovered",
    source_run_id: Optional[int] = None,
) -> tuple[Business, bool]:
    """Returns (business, created). Idempotent: replaying the same record is a no-op."""
    idem = _idempotency_key(connector.id, record)
    existing_record = db.query(SourceRecord).filter(SourceRecord.idempotency_key == idem).first()
    if existing_record and existing_record.business_id:
        return db.get(Business, existing_record.business_id), False
    # A record without a business link means a previous attempt failed mid-way;
    # fall through so the replay can resolve and link the business below.

    business = dedupe.find_existing(db, record)
    created = business is None
    if created:
        business = Business(
            name=record.business_name.strip(),
            category=record.category,
            city=record.city.strip().lower(),
            address=record.address,
            website_url=record.website_url,
            profile_url=record.profile_url,
            dedupe_key=dedupe.dedupe_key(record),
            state=LeadState.NEW.value,
            route=route,
        )
        db.add(business)
        db.flush()

    _upsert_contact(db, business, "email", record.email)
    _upsert_contact(db, business, "phone", record.phone)
    _upsert_contact(db, business, "instagram", record.instagram)

    if existing_record:
        # Repair the orphaned record from the failed earlier attempt.
        existing_record.business_id = business.id
        db.add(existing_record)
    else:
        db.add(
            SourceRecord(
                business_id=business.id,
                source_run_id=source_run_id,
                connector_id=connector.id,
                raw=record.model_dump(mode="json"),
                provenance=record.provenance.model_dump(mode="json"),
                idempotency_key=idem,
            )
        )

    if created:
        record_event(
            db,
            action="business_created",
            actor=f"system:adapter:{connector.adapter}",
            business_id=business.id,
            payload={"route": route, "source": record.provenance.source},
            idempotency_key=f"created:{idem}",
        )
    return business, created


def ingest_inbound(db: Session, connector: SourceConnector, lead: InboundLeadIn) -> tuple[Business, bool]:
    """Inbound route: validate contract → dedupe → MQL (never automatically SQL)."""
    record = SourceRecordIn(
        business_name=lead.business_name,
        category=lead.category,
        city=lead.city or "unknown",
        email=lead.email,
        phone=lead.phone,
        extra={"campaign": lead.campaign, "message": lead.message, "contact_name": lead.contact_name},
        provenance=Provenance(source=lead.source, permitted_use="inbound_consented_contact"),
    )
    business, created = ingest_record(db, connector, record, route="inbound")

    if LeadState(business.state) == LeadState.NEW:
        transition(db, business, LeadState.VALIDATED, actor="system:inbound", reason="inbound contract validated")
        transition(db, business, LeadState.MQL, actor="system:inbound", reason=f"campaign={lead.campaign}")
    return business, created
