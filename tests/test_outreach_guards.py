import pytest

from app.core.schemas import Provenance, SourceRecordIn
from app.core.state_machine import LeadState
from app.models import Consent, Message
from app.services.events import transition
from app.services.ingestion import ingest_record
from app.services.outreach import (
    OutreachBlocked,
    approve_message,
    draft_message,
    mark_sent,
    suppress,
)


def _approved_business(db, connector, name="Spa Guard"):
    record = SourceRecordIn(
        business_name=name,
        category="massage_wellness",
        city="porto",
        email="guard@spa.pt",
        provenance=Provenance(source="test", permitted_use="test"),
    )
    business, _ = ingest_record(db, connector, record)
    for target in (
        LeadState.VALIDATED, LeadState.FITQL, LeadState.SHORTLISTED,
        LeadState.PREVIEW_READY, LeadState.APPROVED,
    ):
        transition(db, business, target, actor="test")
    db.commit()
    return business


def test_send_requires_approval(db, connector):
    business = _approved_business(db, connector)
    message = draft_message(db, business, channel="email", touch_number=1, body="ola", idempotency_key="m1")
    with pytest.raises(OutreachBlocked) as exc:
        mark_sent(db, message, actor="human:op")
    assert exc.value.code == "not_approved"


def test_approved_message_sends_once(db, connector):
    business = _approved_business(db, connector)
    message = draft_message(db, business, channel="email", touch_number=1, body="ola", idempotency_key="m2")
    approve_message(db, message, "operator")
    mark_sent(db, message, actor="human:op")
    db.commit()
    assert message.status == "sent"


def test_suppression_cancels_queued_and_blocks_future(db, connector):
    business = _approved_business(db, connector)
    queued = draft_message(db, business, channel="email", touch_number=1, body="ola", idempotency_key="m3")
    approve_message(db, queued, "operator")
    suppress(db, business, channel=None, reason="opt_out", actor="human:op")
    db.commit()

    assert business.state == "suppressed"
    assert db.get(Message, queued.id).status == "cancelled"

    late = Message(
        business_id=business.id, channel="email", body="x", status="approved", idempotency_key="m4"
    )
    db.add(late)
    db.commit()
    with pytest.raises(OutreachBlocked):
        mark_sent(db, late, actor="human:op")


def test_suppression_writes_state_transition_event(db, connector):
    from app.models import Event

    business = _approved_business(db, connector, name="Spa Event Log")
    suppress(db, business, channel=None, reason="opt_out", actor="human:op")
    db.commit()
    transitions = (
        db.query(Event)
        .filter(Event.business_id == business.id, Event.action == "state_transition")
        .all()
    )
    assert any(e.payload.get("to") == "suppressed" for e in transitions)


def test_whatsapp_requires_opt_in(db, connector):
    business = _approved_business(db, connector, name="Spa WA")
    message = draft_message(db, business, channel="whatsapp", touch_number=1, body="ola", idempotency_key="m5")
    with pytest.raises(OutreachBlocked) as exc:
        approve_message(db, message, "operator")
    assert exc.value.code == "consent_required"

    db.add(Consent(business_id=business.id, channel="whatsapp", basis="opt_in"))
    db.commit()
    approve_message(db, message, "operator")
    assert message.status == "approved"


def test_four_touch_maximum(db, connector):
    business = _approved_business(db, connector, name="Spa Touch")
    for i in range(1, 5):
        m = draft_message(db, business, channel="email", touch_number=i, body="ola", idempotency_key=f"t{i}")
        approve_message(db, m, "operator")
        mark_sent(db, m, actor="human:op")
    db.commit()
    fifth = draft_message(db, business, channel="email", touch_number=5, body="ola", idempotency_key="t5")
    with pytest.raises(OutreachBlocked) as exc:
        approve_message(db, fifth, "operator")
    assert exc.value.code == "touch_limit_reached"


def test_duplicate_idempotency_key_returns_same_message(db, connector):
    business = _approved_business(db, connector, name="Spa Idem")
    m1 = draft_message(db, business, channel="email", touch_number=1, body="ola", idempotency_key="dup")
    m2 = draft_message(db, business, channel="email", touch_number=1, body="ola", idempotency_key="dup")
    assert m1.id == m2.id
    assert db.query(Message).filter(Message.idempotency_key == "dup").count() == 1
