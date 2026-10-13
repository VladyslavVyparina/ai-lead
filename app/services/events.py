"""Immutable event log and guarded state transitions (§3.4)."""

from typing import Optional
import uuid

from sqlalchemy.orm import Session

from app.core.state_machine import LeadState, assert_transition
from app.models import Business, Event


def record_event(
    db: Session,
    *,
    action: str,
    actor: str,
    entity: str = "business",
    business_id: Optional[int] = None,
    entity_id: Optional[int] = None,
    reason: str = "",
    payload: Optional[dict] = None,
    config_version: str = "",
    correlation_id: str = "",
    idempotency_key: Optional[str] = None,
) -> Optional[Event]:
    """Append an event. Returns None if the idempotency key was already used."""
    if idempotency_key is not None:
        existing = db.query(Event).filter(Event.idempotency_key == idempotency_key).first()
        if existing:
            return None
    event = Event(
        business_id=business_id,
        entity=entity,
        entity_id=entity_id if entity_id is not None else business_id,
        action=action,
        actor=actor,
        reason=reason,
        payload=payload or {},
        config_version=config_version,
        correlation_id=correlation_id or str(uuid.uuid4()),
        idempotency_key=idempotency_key,
    )
    db.add(event)
    db.flush()
    return event


def transition(
    db: Session,
    business: Business,
    target: LeadState,
    *,
    actor: str,
    reason: str = "",
    config_version: str = "",
    correlation_id: str = "",
) -> Event:
    current = LeadState(business.state)
    assert_transition(current, target)
    business.state = target.value
    db.add(business)
    event = record_event(
        db,
        action="state_transition",
        actor=actor,
        business_id=business.id,
        reason=reason,
        payload={"from": current.value, "to": target.value},
        config_version=config_version,
        correlation_id=correlation_id,
    )
    return event
