import pytest

from app.core.state_machine import InvalidTransition, LeadState, assert_transition
from app.core.schemas import Provenance, SourceRecordIn
from app.models import Business
from app.services.events import transition
from app.services.ingestion import ingest_record


def _make_business(db, connector, name="Bella Spa") -> Business:
    record = SourceRecordIn(
        business_name=name,
        category="massage_wellness",
        city="porto",
        provenance=Provenance(source="test", permitted_use="test"),
    )
    business, _ = ingest_record(db, connector, record)
    db.commit()
    return business


def test_happy_path_transitions(db, connector):
    b = _make_business(db, connector)
    path = [
        LeadState.VALIDATED, LeadState.FITQL, LeadState.SHORTLISTED,
        LeadState.PREVIEW_READY, LeadState.APPROVED, LeadState.CONTACTED,
        LeadState.ENGAGED, LeadState.SQL, LeadState.WON,
    ]
    for target in path:
        transition(db, b, target, actor="test")
    assert b.state == "won"


def test_invalid_transition_rejected():
    with pytest.raises(InvalidTransition):
        assert_transition(LeadState.NEW, LeadState.CONTACTED)


def test_suppression_reachable_from_any_state():
    for state in LeadState:
        if state is LeadState.SUPPRESSED:
            continue
        assert_transition(state, LeadState.SUPPRESSED)


def test_no_automated_exit_from_suppressed():
    for target in LeadState:
        if target is LeadState.SUPPRESSED:
            continue
        with pytest.raises(InvalidTransition):
            assert_transition(LeadState.SUPPRESSED, target)


def test_transition_writes_immutable_event(db, connector):
    from app.models import Event

    b = _make_business(db, connector)
    transition(db, b, LeadState.VALIDATED, actor="human:tester", reason="checked")
    db.commit()
    event = db.query(Event).filter(Event.action == "state_transition").one()
    assert event.actor == "human:tester"
    assert event.payload == {"from": "new", "to": "validated"}
    assert event.correlation_id
