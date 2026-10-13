"""Outreach guards: approval, consent/channel, suppression, four-touch cap (§7, §8.2).

No send bypasses these checks. Sending itself is human-triggered in P0.
"""

from typing import Optional
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.state_machine import CONTACTABLE_STATES, LeadState
from app.models import Business, Consent, ContactPoint, Message, Suppression
from app.services.config_loader import load
from app.services.events import record_event, transition

MAX_TOUCHES = 4

# Channels that require explicit invitation/opt-in (GTM LOCK).
OPT_IN_ONLY_CHANNELS = {"whatsapp"}


class OutreachBlocked(Exception):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


def is_suppressed(db: Session, business: Business, channel: str) -> bool:
    query = db.query(Suppression).filter(
        Suppression.business_id == business.id,
        (Suppression.channel == channel) | (Suppression.channel.is_(None)),
    )
    if query.first():
        return True
    contact_values = [
        cp.value
        for cp in db.query(ContactPoint).filter(
            ContactPoint.business_id == business.id, ContactPoint.channel == channel
        )
    ]
    if contact_values:
        hit = (
            db.query(Suppression)
            .filter(Suppression.contact_value.in_(contact_values))
            .first()
        )
        if hit:
            return True
    return False


def check_can_send(db: Session, business: Business, channel: str) -> None:
    """Raise OutreachBlocked unless every guard passes."""
    if LeadState(business.state) not in CONTACTABLE_STATES:
        raise OutreachBlocked("state_not_contactable", business.state)

    if is_suppressed(db, business, channel):
        raise OutreachBlocked("suppressed", channel)

    if channel in OPT_IN_ONLY_CHANNELS:
        consent = (
            db.query(Consent)
            .filter(
                Consent.business_id == business.id,
                Consent.channel == channel,
                Consent.revoked_at.is_(None),
            )
            .first()
        )
        if not consent:
            raise OutreachBlocked("consent_required", channel)

    sent_count = (
        db.query(Message)
        .filter(
            Message.business_id == business.id,
            Message.direction == "out",
            Message.status == "sent",
        )
        .count()
    )
    if sent_count >= MAX_TOUCHES:
        raise OutreachBlocked("touch_limit_reached", f"{sent_count}/{MAX_TOUCHES}")


def draft_message(
    db: Session,
    business: Business,
    *,
    channel: str,
    touch_number: int,
    body: str,
    idempotency_key: str,
    research_snapshot: Optional[dict] = None,
) -> Message:
    messaging = load("messaging")
    existing = db.query(Message).filter(Message.idempotency_key == idempotency_key).first()
    if existing:
        return existing
    message = Message(
        business_id=business.id,
        channel=channel,
        touch_number=touch_number,
        copy_version=messaging["_version"],
        body=body,
        research_snapshot=research_snapshot or {},
        status="draft",
        idempotency_key=idempotency_key,
    )
    db.add(message)
    db.flush()
    record_event(
        db,
        action="message_drafted",
        actor="system:outreach",
        business_id=business.id,
        entity="message",
        entity_id=message.id,
        config_version=messaging["_version"],
        payload={"personalized": bool(research_snapshot)},
    )
    return message


def draft_personalized_message(
    db: Session,
    business: Business,
    *,
    channel: str = "email",
    touch_number: int = 1,
    preview_url: str = "",
) -> Message:
    """AI/research-grounded outreach draft. Still requires human approval before send."""
    from app.services.ai_agent import draft_personalized_outreach
    from app.services.content_mapping import build_business_profile

    profile = build_business_profile(db, business)
    body, research, _cost = draft_personalized_outreach(
        profile, preview_url=preview_url, channel=channel
    )
    return draft_message(
        db,
        business,
        channel=channel,
        touch_number=touch_number,
        body=body,
        idempotency_key=f"personalized:{business.id}:{channel}:{touch_number}",
        research_snapshot=research,
    )


def approve_message(db: Session, message: Message, approver: str) -> Message:
    business = db.get(Business, message.business_id)
    # Human gate after preview: approving outreach implies accepting the preview package.
    if LeadState(business.state) == LeadState.PREVIEW_READY:
        transition(
            db,
            business,
            LeadState.APPROVED,
            actor=f"human:{approver}",
            reason="preview accepted via outreach approval",
        )
    check_can_send(db, business, message.channel)
    message.status = "approved"
    message.approved_by = approver
    db.add(message)
    record_event(
        db,
        action="message_approved",
        actor=f"human:{approver}",
        business_id=business.id,
        entity="message",
        entity_id=message.id,
    )
    return message


def mark_sent(db: Session, message: Message, actor: str) -> Message:
    if message.status != "approved":
        raise OutreachBlocked("not_approved", message.status)
    business = db.get(Business, message.business_id)
    # Re-check at send time: revoked approval/suppression invalidates queued messages.
    check_can_send(db, business, message.channel)
    message.status = "sent"
    message.sent_at = datetime.utcnow()
    db.add(message)
    record_event(
        db,
        action="message_sent",
        actor=actor,
        business_id=business.id,
        entity="message",
        entity_id=message.id,
        idempotency_key=f"sent:{message.idempotency_key}",
    )
    return message


def suppress(db: Session, business: Business, *, channel: Optional[str], reason: str, actor: str) -> None:
    db.add(Suppression(business_id=business.id, channel=channel, reason=reason))
    cancelled = (
        db.query(Message)
        .filter(
            Message.business_id == business.id,
            Message.status.in_(["draft", "queued", "approved"]),
        )
        .all()
    )
    for msg in cancelled:
        msg.status = "cancelled"
        db.add(msg)
    # Go through the state machine so the transition itself is logged too.
    transition(db, business, LeadState.SUPPRESSED, actor=actor, reason=reason)
    record_event(
        db,
        action="suppressed",
        actor=actor,
        business_id=business.id,
        reason=reason,
        payload={"channel": channel, "cancelled_messages": len(cancelled)},
    )
