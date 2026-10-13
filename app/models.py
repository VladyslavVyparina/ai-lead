"""Canonical entities (architecture §3.3).

Groups:
- Configuration: SourceConnector, CampaignRun, SourceRun (markets/pricebook/
  messaging live in versioned YAML under config/, not in the DB).
- Lead intelligence: Business, ContactPoint, SourceRecord, Evidence,
  Qualification, WebsiteAudit.
- Assets/commercial: WebsiteBuild, Proposal.
- Engagement: Consent, Suppression, Message, Event, Opportunity.
"""

from datetime import datetime, timedelta

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


def _now() -> datetime:
    return datetime.utcnow()


# --- Configuration -----------------------------------------------------------


class SourceConnector(Base):
    """A configured instance of an approved source adapter (§2.2)."""

    __tablename__ = "source_connectors"
    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False, unique=True)
    adapter = Column(String(64), nullable=False)  # csv_import | inbound_webhook | directory_seed
    owner = Column(String(255), nullable=False)
    permitted_use = Column(Text, nullable=False)
    field_mapping = Column(JSON, default=dict)
    cities = Column(JSON, default=list)
    category = Column(String(128), default="")  # e.g. massage_wellness
    catalog_ref = Column(String(255), default="")  # config key for directory_seed catalogs
    quota_per_run = Column(Integer, default=500)
    retention_days = Column(Integer, default=180)
    credentials_ref = Column(String(255), default="")  # reference into managed secrets, never a secret
    cursor = Column(String(255), default="")  # opaque resume cursor for incremental runs
    record_count = Column(Integer, default=0)
    error_count = Column(Integer, default=0)
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_now)


class CampaignRun(Base):
    __tablename__ = "campaign_runs"
    id = Column(Integer, primary_key=True)
    market = Column(String(64), nullable=False)  # porto | lisbon | braga
    config_versions = Column(JSON, default=dict)  # {"scoring": "v1", "pricebook": "v1", ...}
    status = Column(String(32), default="running")
    started_at = Column(DateTime, default=_now)
    finished_at = Column(DateTime, nullable=True)


class SourceRun(Base):
    """One connector execution; resumable from checkpoint (§3.4)."""

    __tablename__ = "source_runs"
    id = Column(Integer, primary_key=True)
    connector_id = Column(Integer, ForeignKey("source_connectors.id"), nullable=False)
    campaign_run_id = Column(Integer, ForeignKey("campaign_runs.id"), nullable=True)
    status = Column(String(32), default="running")  # running | failed | completed
    checkpoint = Column(Integer, default=0)  # last processed record index
    payload_sha256 = Column(String(64), default="")  # guards resume against a different file
    records_ingested = Column(Integer, default=0)
    records_skipped = Column(Integer, default=0)
    error_count = Column(Integer, default=0)
    cursor_after = Column(String(255), default="")
    error = Column(Text, nullable=True)
    started_at = Column(DateTime, default=_now)
    finished_at = Column(DateTime, nullable=True)

    connector = relationship("SourceConnector")


# --- Lead intelligence --------------------------------------------------------


class Business(Base):
    __tablename__ = "businesses"
    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_business_dedupe"),)

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    category = Column(String(128), nullable=False)  # beauty_aesthetics | massage_wellness | ...
    city = Column(String(64), nullable=False)
    address = Column(String(512), default="")
    website_url = Column(String(1024), default="")
    profile_url = Column(String(1024), default="")
    dedupe_key = Column(String(512), nullable=False, index=True)
    state = Column(String(32), default="new", index=True)
    route = Column(String(32), default="discovered")  # discovered | inbound | referral
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    contact_points = relationship("ContactPoint", back_populates="business", cascade="all, delete-orphan")
    source_records = relationship("SourceRecord", back_populates="business")
    evidence = relationship("Evidence", back_populates="business", cascade="all, delete-orphan")
    qualifications = relationship("Qualification", back_populates="business", cascade="all, delete-orphan")
    events = relationship("Event", back_populates="business", cascade="all, delete-orphan")


class ContactPoint(Base):
    __tablename__ = "contact_points"
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=False)
    channel = Column(String(32), nullable=False)  # email | phone | form | instagram | linkedin | whatsapp
    value = Column(String(512), nullable=False, index=True)
    # Canonical form for dedupe lookups (digits-only phone, lowercased email).
    normalized_value = Column(String(512), nullable=False, default="", index=True)
    is_business_contact = Column(Boolean, default=True)  # business vs personal (§4.2 permission)
    verified = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_now)

    business = relationship("Business", back_populates="contact_points")


class SourceRecord(Base):
    """Raw record with mandatory provenance and permission metadata (§2.2)."""

    __tablename__ = "source_records"
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=True)
    source_run_id = Column(Integer, ForeignKey("source_runs.id"), nullable=True)
    connector_id = Column(Integer, ForeignKey("source_connectors.id"), nullable=False)
    raw = Column(JSON, nullable=False)
    provenance = Column(JSON, nullable=False)  # {source, url, captured_at, permitted_use}
    idempotency_key = Column(String(255), nullable=False, unique=True)
    created_at = Column(DateTime, default=_now)

    business = relationship("Business", back_populates="source_records")


class Evidence(Base):
    """Every generated fact links here (§3.4). Missing facts are omitted, never invented."""

    __tablename__ = "evidence"
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=False)
    fact_key = Column(String(128), nullable=False)  # e.g. services, prices, hours, rating
    fact_value = Column(JSON, nullable=False)
    source_url = Column(String(1024), default="")
    confidence = Column(Float, default=1.0)
    # confirmed | inferred | missing | conflicting | stale
    status = Column(String(32), default="confirmed")
    captured_at = Column(DateTime, default=_now)

    business = relationship("Business", back_populates="evidence")


class WebsiteAudit(Base):
    __tablename__ = "website_audits"
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=False)
    has_website = Column(Boolean, default=False)
    mobile_ok = Column(Boolean, default=False)
    has_booking_cta = Column(Boolean, default=False)
    services_clear = Column(Boolean, default=False)
    contact_clear = Column(Boolean, default=False)
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=_now)


class Qualification(Base):
    """FitQL score against a versioned rubric (§4.3)."""

    __tablename__ = "qualifications"
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=False)
    rubric_version = Column(String(32), nullable=False)
    scores = Column(JSON, nullable=False)  # per-dimension raw scores 0..100
    breakdown = Column(JSON, default=dict)  # weighted contribution points toward total
    reasons = Column(JSON, default=list)  # human-readable why Tier A / routing
    total = Column(Float, nullable=False)
    routing = Column(String(32), nullable=False)  # priority_review | manual_nurture | reject
    tier = Column(String(8), nullable=True)  # A | B
    created_at = Column(DateTime, default=_now)

    business = relationship("Business", back_populates="qualifications")


# --- Assets / commercial ------------------------------------------------------


class WebsiteBuild(Base):
    __tablename__ = "website_builds"
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=False)
    template_id = Column(String(128), nullable=False)
    template_version = Column(String(32), nullable=False)
    site_config = Column(JSON, nullable=False)  # validated SiteConfig (§5.2)
    content_pack = Column(JSON, default=dict)  # structured AI/template content (not React)
    mode = Column(String(16), default="preview")  # preview | production
    status = Column(String(32), default="pending")  # pending | building | qa | deployed | failed | expired
    # preview pipeline stages (template-driven, not LLM-rewritten repos)
    pipeline_stage = Column(
        String(64),
        default="pending",
    )  # approved|preview_generation_queued|content_generated|assets_generated|build_started|deployed|validation_passed|preview_ready|failed
    preview_url = Column(String(1024), default="")
    qa_report = Column(JSON, default=dict)
    cost_units = Column(Float, default=0.0)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class PreviewSubmission(Base):
    """A preview-site form submission with finite retention.

    Contact data is kept out of the immutable Event payload so it can be
    deleted when retention expires.
    """

    __tablename__ = "preview_submissions"
    id = Column(Integer, primary_key=True)
    website_build_id = Column(Integer, ForeignKey("website_builds.id"), nullable=False)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    contact = Column(String(200), nullable=False)
    message = Column(Text, default="")
    status = Column(String(32), default="new")  # new | handled | spam | duplicate
    dedupe_key = Column(String(255), default="", index=True)
    attribution = Column(JSON, default=dict)  # preview version, UTM, source metadata
    created_at = Column(DateTime, default=_now)
    expires_at = Column(DateTime, default=lambda: _now() + timedelta(days=90), nullable=False)


class Proposal(Base):
    __tablename__ = "proposals"
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=False)
    template_version = Column(String(32), nullable=False)
    pricebook_version = Column(String(32), nullable=False)
    offer_package = Column(String(64), nullable=False)  # basic | local_pro | automation_pro
    content = Column(JSON, nullable=False)  # rendered sections with evidence refs
    status = Column(String(32), default="draft")  # draft | approved | sent
    created_at = Column(DateTime, default=_now)


# --- Engagement ---------------------------------------------------------------


class Consent(Base):
    __tablename__ = "consents"
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=False)
    channel = Column(String(32), nullable=False)
    basis = Column(String(64), nullable=False)  # opt_in | invited | inbound_request | legitimate_interest
    granted_at = Column(DateTime, default=_now)
    revoked_at = Column(DateTime, nullable=True)


class Suppression(Base):
    """Overrides retries, follow-ups and schedules (§3.4)."""

    __tablename__ = "suppressions"
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=True)
    channel = Column(String(32), nullable=True)  # null = all channels
    contact_value = Column(String(512), nullable=True, index=True)
    reason = Column(String(255), nullable=False)  # opt_out | bounce | legal | manual
    created_at = Column(DateTime, default=_now)


class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=False)
    channel = Column(String(32), nullable=False)
    direction = Column(String(8), default="out")  # out | in
    touch_number = Column(Integer, default=1)  # 1..4, four-touch maximum (§7.3)
    copy_version = Column(String(32), default="")
    body = Column(Text, nullable=False)
    research_snapshot = Column(JSON, default=dict)  # facts used for personalisation
    status = Column(String(32), default="draft")  # draft | queued | approved | sent | cancelled
    approved_by = Column(String(255), nullable=True)
    idempotency_key = Column(String(255), unique=True, nullable=False)
    sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now)


class PipelineJob(Base):
    """Durable automation job with retry/idempotency (§ pipeline observability)."""

    __tablename__ = "pipeline_jobs"
    id = Column(Integer, primary_key=True)
    job_type = Column(String(64), nullable=False)  # connector_run | enrich | score | preview | outreach_draft | ...
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=True, index=True)
    connector_id = Column(Integer, ForeignKey("source_connectors.id"), nullable=True)
    source_run_id = Column(Integer, ForeignKey("source_runs.id"), nullable=True)
    status = Column(String(32), default="queued")  # queued | running | succeeded | failed | cancelled
    stage = Column(String(64), default="")
    attempts = Column(Integer, default=0)
    max_attempts = Column(Integer, default=3)
    cost_units = Column(Float, default=0.0)
    idempotency_key = Column(String(255), unique=True, nullable=False)
    input_payload = Column(JSON, default=dict)
    result = Column(JSON, default=dict)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)
    finished_at = Column(DateTime, nullable=True)


class Event(Base):
    """Immutable audit log; one row per transition/action (§3.4)."""

    __tablename__ = "events"
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=True)
    entity = Column(String(64), nullable=False)
    entity_id = Column(Integer, nullable=True)
    action = Column(String(64), nullable=False)  # e.g. state_transition, message_sent
    actor = Column(String(255), nullable=False)  # system:<component> or human:<name>
    reason = Column(Text, default="")
    payload = Column(JSON, default=dict)
    config_version = Column(String(64), default="")
    correlation_id = Column(String(128), index=True, default="")
    idempotency_key = Column(String(255), unique=True, nullable=True)
    created_at = Column(DateTime, default=_now)

    business = relationship("Business", back_populates="events")


class Opportunity(Base):
    __tablename__ = "opportunities"
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=False)
    stage = Column(String(32), default="qualified_conversation")  # qualified_conversation | meeting | proposal | won | lost
    value_eur = Column(Float, nullable=True)
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)
