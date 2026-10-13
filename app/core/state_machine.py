"""Lead lifecycle state machine (architecture §3.2).

Every transition must go through `assert_transition`; callers then write an
immutable Event row. Suppression is terminal for marketing purposes and can be
entered from any state.
"""

from enum import Enum


class LeadState(str, Enum):
    NEW = "new"
    VALIDATED = "validated"
    MQL = "mql"
    FITQL = "fitql"
    REJECTED = "rejected"
    SHORTLISTED = "shortlisted"
    NURTURE = "nurture"
    PREVIEW_READY = "preview_ready"
    REVISION = "revision"
    APPROVED = "approved"
    CONTACTED = "contacted"
    ENGAGED = "engaged"
    FOLLOW_UP = "follow_up"
    SQL = "sql"
    MEETING = "meeting"
    WON = "won"
    LOST = "lost"
    SUPPRESSED = "suppressed"


TRANSITIONS: dict[LeadState, set[LeadState]] = {
    LeadState.NEW: {LeadState.VALIDATED, LeadState.REJECTED},
    LeadState.VALIDATED: {LeadState.MQL, LeadState.FITQL, LeadState.REJECTED},
    LeadState.MQL: {LeadState.SHORTLISTED, LeadState.NURTURE, LeadState.REJECTED},
    LeadState.FITQL: {LeadState.SHORTLISTED, LeadState.NURTURE, LeadState.REJECTED},
    LeadState.SHORTLISTED: {LeadState.PREVIEW_READY, LeadState.REJECTED},
    LeadState.PREVIEW_READY: {LeadState.APPROVED, LeadState.REVISION},
    LeadState.REVISION: {LeadState.PREVIEW_READY, LeadState.REJECTED},
    LeadState.APPROVED: {LeadState.CONTACTED},
    LeadState.CONTACTED: {LeadState.ENGAGED, LeadState.FOLLOW_UP},
    LeadState.FOLLOW_UP: {LeadState.ENGAGED, LeadState.NURTURE, LeadState.LOST},
    LeadState.ENGAGED: {LeadState.SQL, LeadState.NURTURE, LeadState.LOST},
    LeadState.SQL: {LeadState.MEETING, LeadState.WON, LeadState.LOST, LeadState.NURTURE},
    LeadState.MEETING: {LeadState.WON, LeadState.LOST, LeadState.NURTURE},
    LeadState.NURTURE: {LeadState.SHORTLISTED, LeadState.REJECTED},
    LeadState.REJECTED: set(),
    LeadState.WON: set(),
    LeadState.LOST: {LeadState.NURTURE},
    # Manual/legal reactivation only — no automated transitions out.
    LeadState.SUPPRESSED: set(),
}

# States in which an outbound message may be queued (before approval checks).
CONTACTABLE_STATES = {
    LeadState.APPROVED,
    LeadState.CONTACTED,
    LeadState.FOLLOW_UP,
    LeadState.ENGAGED,
}


class InvalidTransition(Exception):
    def __init__(self, current: LeadState, target: LeadState):
        self.current = current
        self.target = target
        super().__init__(f"Invalid transition: {current.value} -> {target.value}")


def assert_transition(current: LeadState, target: LeadState) -> None:
    # Suppression overrides everything and is reachable from any state.
    if target is LeadState.SUPPRESSED:
        return
    if target not in TRANSITIONS.get(current, set()):
        raise InvalidTransition(current, target)
