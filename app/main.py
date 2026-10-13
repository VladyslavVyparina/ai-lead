"""AI Lead Pipeline — control plane API.

Deterministic orchestrator endpoints: source connectors, CSV import, generic
inbound webhook (P0 contract), qualification, website/proposal factory,
approval-gated outreach, config versioning and KPI analytics.
"""

from typing import Optional
import logging
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.core.schemas import InboundLeadIn
from app.core.state_machine import InvalidTransition, LeadState
from app.database import Base, engine, get_db
from app.migrations import backfill_normalized_contacts, migrate_sqlite
from app.models import (
    Business,
    ContactPoint,
    Evidence,
    Message,
    PipelineJob,
    PreviewSubmission,
    Qualification,
    SourceConnector,
    SourceRun,
    WebsiteAudit,
    WebsiteBuild,
)
from app.adapters.csv_import import PayloadMismatch, run_import
from app.adapters.directory_seed import ensure_porto_wellness_connector
from app.services import config_loader
from app.services.analytics import kpi_snapshot
from app.services.events import record_event, transition
from app.services.ingestion import ingest_inbound
from app.services.outreach import (
    OutreachBlocked,
    approve_message,
    draft_message,
    draft_personalized_message,
    mark_sent,
    suppress,
)
from app.services.pipeline import (
    approve_and_generate_preview,
    discover_enrich_score,
    run_porto_wellness_vertical_slice,
)
from app.services.proposal import PricebookError, build_proposal
from app.services.preview_forms import rate_limit_exceeded, valid_form_token
from app.services.scoring import assign_tiers, score_business
from app.services.site_factory import FactValidationError, build_and_deploy_preview, expire_previews
import hashlib

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")

from app.ui import router as ui_router

app = FastAPI(title="AI Lead Pipeline", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=settings.preview_origin_regex,
    allow_credentials=False,
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)
Base.metadata.create_all(bind=engine)
migrate_sqlite(engine)
backfill_normalized_contacts(engine)
app.include_router(ui_router, prefix="/ui", include_in_schema=False)

logger = logging.getLogger("ai_lead.api")

_DEFAULT_SECRETS = {"change_this_token", "change_this_secret", ""}
if settings.admin_token in _DEFAULT_SECRETS or settings.inbound_webhook_secret in _DEFAULT_SECRETS:
    logger.warning(
        "SECURITY: ADMIN_TOKEN and/or INBOUND_WEBHOOK_SECRET still use default values. "
        "Set strong secrets in .env before exposing this service."
    )


def require_admin(x_admin_token: str = Header(default="")) -> None:
    if not secrets.compare_digest(x_admin_token, settings.admin_token):
        raise HTTPException(status_code=401, detail="invalid admin token")


def require_webhook_secret(x_webhook_secret: str = Header(default="")) -> None:
    if not secrets.compare_digest(x_webhook_secret, settings.inbound_webhook_secret):
        raise HTTPException(status_code=401, detail="invalid webhook secret")


def get_business(db: Session, business_id: int) -> Business:
    business = db.get(Business, business_id)
    if not business:
        raise HTTPException(status_code=404, detail="business not found")
    return business


@app.get("/")
def root():
    return RedirectResponse("/ui", status_code=307)


@app.get("/health")
def health():
    return {"ok": True}


# --- Source connectors and ingestion -----------------------------------------


class ConnectorIn(BaseModel):
    name: str
    adapter: str  # csv_import | inbound_webhook | directory_seed
    owner: str
    permitted_use: str
    field_mapping: dict = {}
    cities: list[str] = []
    category: str = ""
    catalog_ref: str = ""
    quota_per_run: int = 500
    retention_days: int = 180


@app.post("/connectors", dependencies=[Depends(require_admin)])
def create_connector(payload: ConnectorIn, db: Session = Depends(get_db)):
    connector = SourceConnector(**payload.model_dump())
    db.add(connector)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"connector name already exists: {payload.name}")
    db.refresh(connector)
    return {"id": connector.id, "name": connector.name}


@app.get("/connectors", dependencies=[Depends(require_admin)])
def list_connectors(db: Session = Depends(get_db)):
    return [
        {
            "id": c.id,
            "name": c.name,
            "adapter": c.adapter,
            "active": c.is_active,
            "city": (c.cities or [None])[0],
            "category": c.category,
            "record_count": c.record_count,
            "error_count": c.error_count,
            "last_run_at": c.last_run_at.isoformat() if c.last_run_at else None,
            "next_run_at": c.next_run_at.isoformat() if c.next_run_at else None,
            "cursor": c.cursor,
        }
        for c in db.query(SourceConnector).all()
    ]


@app.post("/connectors/porto-wellness/ensure", dependencies=[Depends(require_admin)])
def ensure_porto_connector(db: Session = Depends(get_db)):
    connector = ensure_porto_wellness_connector(db)
    return {"id": connector.id, "name": connector.name, "adapter": connector.adapter}


@app.post("/connectors/{connector_id}/run", dependencies=[Depends(require_admin)])
def run_connector_pipeline(
    connector_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    top_review: int = Query(default=5, ge=1, le=15),
    db: Session = Depends(get_db),
):
    """Run discovery → enrich → FitQL → shortlist top N for preview approval."""
    connector = db.get(SourceConnector, connector_id)
    if not connector or not connector.is_active:
        raise HTTPException(status_code=404, detail="connector not found or inactive")
    if connector.adapter != "directory_seed":
        raise HTTPException(status_code=400, detail="only directory_seed supports /run automation")
    try:
        result = discover_enrich_score(db, connector, limit=limit, top_review=top_review)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return result


@app.post("/pipeline/porto-wellness", dependencies=[Depends(require_admin)])
def porto_wellness_slice(
    limit: int = Query(default=20, ge=1, le=100),
    top_review: int = Query(default=5, ge=1, le=15),
    db: Session = Depends(get_db),
):
    """One-shot vertical slice for the Porto massage/wellness pilot."""
    try:
        return run_porto_wellness_vertical_slice(db, limit=limit, top_review=top_review)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/pipeline/jobs", dependencies=[Depends(require_admin)])
def pipeline_jobs(limit: int = Query(default=50, ge=1, le=200), db: Session = Depends(get_db)):
    jobs = db.query(PipelineJob).order_by(PipelineJob.id.desc()).limit(limit).all()
    return [
        {
            "id": j.id,
            "job_type": j.job_type,
            "status": j.status,
            "stage": j.stage,
            "business_id": j.business_id,
            "attempts": j.attempts,
            "cost_units": j.cost_units,
            "error": j.error,
            "result": j.result,
        }
        for j in jobs
    ]


MAX_CSV_BYTES = 20 * 1024 * 1024  # 20 MB is far beyond any pilot batch


async def _read_csv_upload(file: UploadFile) -> str:
    payload = await file.read()
    if len(payload) > MAX_CSV_BYTES:
        raise HTTPException(status_code=413, detail="csv file too large (max 20 MB)")
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="file is not valid UTF-8 text; export the CSV as UTF-8")


@app.post("/connectors/{connector_id}/import-csv", dependencies=[Depends(require_admin)])
async def import_csv(connector_id: int, file: UploadFile, db: Session = Depends(get_db)):
    connector = db.get(SourceConnector, connector_id)
    if not connector or not connector.is_active:
        raise HTTPException(status_code=404, detail="connector not found or inactive")
    csv_text = await _read_csv_upload(file)
    run = run_import(db, connector, csv_text)
    return {
        "run_id": run.id,
        "status": run.status,
        "ingested": run.records_ingested,
        "skipped": run.records_skipped,
        "checkpoint": run.checkpoint,
        "error": run.error,
    }


@app.post("/source-runs/{run_id}/resume", dependencies=[Depends(require_admin)])
async def resume_run(run_id: int, file: UploadFile, db: Session = Depends(get_db)):
    run = db.get(SourceRun, run_id)
    if not run or run.status != "failed":
        raise HTTPException(status_code=404, detail="failed run not found")
    connector = db.get(SourceConnector, run.connector_id)
    csv_text = await _read_csv_upload(file)
    try:
        run = run_import(db, connector, csv_text, resume_run=run)
    except PayloadMismatch:
        raise HTTPException(
            status_code=409,
            detail="uploaded file differs from the original import; resume requires the same CSV",
        )
    return {"run_id": run.id, "status": run.status, "ingested": run.records_ingested, "checkpoint": run.checkpoint}


@app.post("/webhooks/inbound", dependencies=[Depends(require_webhook_secret)])
def inbound_webhook(payload: InboundLeadIn, db: Session = Depends(get_db)):
    """Generic inbound contract (P0). Meta Lead Ads maps here in P1."""
    connector = (
        db.query(SourceConnector)
        .filter(SourceConnector.adapter == "inbound_webhook", SourceConnector.is_active.is_(True))
        .first()
    )
    if not connector:
        raise HTTPException(status_code=503, detail="no active inbound connector configured")
    business, created = ingest_inbound(db, connector, payload)
    db.commit()
    return {"ok": True, "business_id": business.id, "created": created, "state": business.state}


class PreviewFormIn(BaseModel):
    build_id: int = Field(gt=0)
    form_token: str = Field(min_length=64, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    contact: str = Field(min_length=3, max_length=200)
    message: str = Field(default="", max_length=2000)
    utm_source: str = Field(default="", max_length=100)
    utm_medium: str = Field(default="", max_length=100)
    utm_campaign: str = Field(default="", max_length=100)


@app.post("/preview-form")
def preview_form(payload: PreviewFormIn, request: Request, db: Session = Depends(get_db)):
    """Controlled operator endpoint for preview-site booking/contact forms (§5.3).

    Public by design (forms on preview sites can't hold secrets); it only logs
    an event for the operator and never routes anything to the business.
    """
    client_key = request.client.host if request.client else "unknown"
    if rate_limit_exceeded(client_key):
        raise HTTPException(status_code=429, detail="too many form submissions")
    build = db.get(WebsiteBuild, payload.build_id)
    if not build:
        raise HTTPException(status_code=404, detail="preview build not found")
    if build.status not in {"building", "deployed"}:
        raise HTTPException(status_code=410, detail="preview is no longer active")
    if not valid_form_token(build.id, payload.form_token):
        raise HTTPException(status_code=403, detail="invalid preview form token")

    dedupe_key = hashlib.sha256(
        f"{build.id}|{payload.contact.strip().lower()}|{payload.message.strip()[:200]}".encode()
    ).hexdigest()
    existing = (
        db.query(PreviewSubmission)
        .filter(PreviewSubmission.dedupe_key == dedupe_key)
        .order_by(PreviewSubmission.created_at.desc())
        .first()
    )
    if existing:
        existing.status = "duplicate"
        db.add(existing)
        record_event(
            db,
            action="preview_form_duplicate",
            actor="system:preview_site",
            business_id=build.business_id,
            entity="preview_submission",
            entity_id=existing.id,
            payload={"build_id": build.id, "grouped_with": existing.id},
        )
        db.commit()
        return {"ok": True, "duplicate": True, "submission_id": existing.id}

    attribution = {
        "preview_version": build.id,
        "pipeline_stage": build.pipeline_stage,
        "template_id": build.template_id,
        "template_version": build.template_version,
        "utm_source": payload.utm_source,
        "utm_medium": payload.utm_medium,
        "utm_campaign": payload.utm_campaign,
    }
    submission = PreviewSubmission(
        website_build_id=build.id,
        business_id=build.business_id,
        name=payload.name,
        contact=payload.contact,
        message=payload.message,
        dedupe_key=dedupe_key,
        attribution=attribution,
    )
    db.add(submission)
    db.flush()
    business = db.get(Business, build.business_id)
    if business and LeadState(business.state) in {LeadState.PREVIEW_READY, LeadState.APPROVED, LeadState.CONTACTED}:
        # Inbound interest signal — do not auto-SQL
        record_event(
            db,
            action="preview_lead_capture",
            actor="system:preview_site",
            business_id=business.id,
            entity="preview_submission",
            entity_id=submission.id,
            payload={"build_id": build.id},
        )
    record_event(
        db,
        action="preview_form_submission",
        actor="system:preview_site",
        business_id=build.business_id,
        entity="website_build",
        entity_id=build.id,
        payload={
            "submission_id": submission.id,
            "build_id": build.id,
            "attribution": attribution,
        },
    )
    db.commit()
    return {"ok": True, "submission_id": submission.id}


# --- Verification, evidence and qualification ---------------------------------


class EvidenceIn(BaseModel):
    fact_key: str
    fact_value: object
    source_url: str = ""
    confidence: float = 1.0


@app.post("/businesses/{business_id}/evidence", dependencies=[Depends(require_admin)])
def add_evidence(business_id: int, items: list[EvidenceIn], db: Session = Depends(get_db)):
    business = get_business(db, business_id)
    for item in items:
        db.add(Evidence(business_id=business.id, **item.model_dump()))
    db.commit()
    return {"ok": True, "count": len(items)}


class AuditIn(BaseModel):
    has_website: bool = False
    mobile_ok: bool = False
    has_booking_cta: bool = False
    services_clear: bool = False
    contact_clear: bool = False
    notes: str = ""


@app.post("/businesses/{business_id}/audit", dependencies=[Depends(require_admin)])
def add_audit(business_id: int, payload: AuditIn, db: Session = Depends(get_db)):
    business = get_business(db, business_id)
    db.add(WebsiteAudit(business_id=business.id, **payload.model_dump()))
    db.commit()
    return {"ok": True}


@app.post("/businesses/{business_id}/contacts/{contact_id}/verify", dependencies=[Depends(require_admin)])
def verify_contact(business_id: int, contact_id: int, db: Session = Depends(get_db)):
    """Operator confirms a contact path is real and permitted (§4.2 contactability)."""
    contact = db.get(ContactPoint, contact_id)
    if not contact or contact.business_id != business_id:
        raise HTTPException(status_code=404, detail="contact not found for this business")
    contact.verified = True
    db.add(contact)
    record_event(
        db,
        action="contact_verified",
        actor="human:operator",
        business_id=business_id,
        entity="contact_point",
        entity_id=contact.id,
        payload={"channel": contact.channel},
    )
    db.commit()
    return {"ok": True, "channel": contact.channel, "verified": True}


class TransitionIn(BaseModel):
    target: LeadState
    actor: str
    reason: str = ""


@app.post("/businesses/{business_id}/transition", dependencies=[Depends(require_admin)])
def transition_business(business_id: int, payload: TransitionIn, db: Session = Depends(get_db)):
    business = get_business(db, business_id)
    try:
        transition(db, business, payload.target, actor=payload.actor, reason=payload.reason)
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    db.commit()
    return {"ok": True, "state": business.state}


@app.post("/businesses/{business_id}/score", dependencies=[Depends(require_admin)])
def score(business_id: int, db: Session = Depends(get_db)):
    business = get_business(db, business_id)
    qualification = score_business(db, business)
    if LeadState(business.state) == LeadState.NEW:
        transition(db, business, LeadState.VALIDATED, actor="system:scoring", reason="scored")
    if LeadState(business.state) == LeadState.VALIDATED:
        target = LeadState.FITQL if qualification.routing != "reject" else LeadState.REJECTED
        transition(db, business, target, actor="system:scoring", reason=f"total={qualification.total}")
    db.commit()
    return {
        "total": qualification.total,
        "scores": qualification.scores,
        "breakdown": qualification.breakdown,
        "reasons": qualification.reasons,
        "routing": qualification.routing,
        "rubric_version": qualification.rubric_version,
        "state": business.state,
    }


@app.post("/businesses/{business_id}/approve-preview", dependencies=[Depends(require_admin)])
def approve_preview(business_id: int, db: Session = Depends(get_db)):
    """Human gate: approve shortlisted lead → template-driven preview + outreach draft."""
    business = get_business(db, business_id)
    try:
        result = approve_and_generate_preview(db, business, actor="human:operator")
    except FactValidationError as exc:
        raise HTTPException(status_code=422, detail={"code": "low_confidence_facts", "facts": exc.flagged})
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return result


@app.post("/businesses/{business_id}/outreach-draft", dependencies=[Depends(require_admin)])
def create_personalized_draft(business_id: int, channel: str = "email", db: Session = Depends(get_db)):
    business = get_business(db, business_id)
    latest_build = (
        db.query(WebsiteBuild)
        .filter(WebsiteBuild.business_id == business.id, WebsiteBuild.status == "deployed")
        .order_by(WebsiteBuild.created_at.desc())
        .first()
    )
    message = draft_personalized_message(
        db,
        business,
        channel=channel,
        touch_number=1,
        preview_url=latest_build.preview_url if latest_build else "",
    )
    db.commit()
    return {
        "message_id": message.id,
        "status": message.status,
        "body": message.body,
        "research_snapshot": message.research_snapshot,
    }


@app.post("/qualifications/assign-tiers", dependencies=[Depends(require_admin)])
def tiers(db: Session = Depends(get_db)):
    result = assign_tiers(db)
    db.commit()
    return result


@app.get("/businesses", dependencies=[Depends(require_admin)])
def list_businesses(
    state: Optional[str] = None,
    city: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    query = db.query(Business)
    if state:
        query = query.filter(Business.state == state)
    if city:
        query = query.filter(Business.city == city)
    total = query.count()
    items = query.order_by(Business.id).offset(offset).limit(limit).all()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {"id": b.id, "name": b.name, "category": b.category, "city": b.city, "state": b.state, "route": b.route}
            for b in items
        ],
    }


@app.get("/businesses/{business_id}", dependencies=[Depends(require_admin)])
def business_detail(business_id: int, db: Session = Depends(get_db)):
    b = get_business(db, business_id)
    latest_q = (
        db.query(Qualification)
        .filter(Qualification.business_id == b.id)
        .order_by(Qualification.created_at.desc())
        .first()
    )
    return {
        "id": b.id,
        "name": b.name,
        "category": b.category,
        "city": b.city,
        "state": b.state,
        "route": b.route,
        "contacts": [
            {"id": c.id, "channel": c.channel, "value": c.value, "verified": c.verified}
            for c in db.query(ContactPoint).filter(ContactPoint.business_id == b.id)
        ],
        "qualification": (
            {
                "total": latest_q.total,
                "routing": latest_q.routing,
                "tier": latest_q.tier,
                "breakdown": latest_q.breakdown,
                "reasons": latest_q.reasons,
                "scores": latest_q.scores,
            }
            if latest_q
            else None
        ),
    }


# --- Website and proposal factory ---------------------------------------------


@app.post("/businesses/{business_id}/build-preview", dependencies=[Depends(require_admin)])
def build_preview(business_id: int, db: Session = Depends(get_db)):
    business = get_business(db, business_id)
    try:
        build = build_and_deploy_preview(db, business, actor="human:operator")
    except FactValidationError as exc:
        raise HTTPException(status_code=422, detail={"code": "low_confidence_facts", "facts": exc.flagged})
    if build.status == "deployed" and LeadState(business.state) == LeadState.SHORTLISTED:
        transition(db, business, LeadState.PREVIEW_READY, actor="system:site_factory", reason=build.preview_url)
    db.commit()
    return {
        "build_id": build.id,
        "status": build.status,
        "preview_url": build.preview_url,
        "qa": build.qa_report,
        "expires_at": build.expires_at.isoformat() if build.expires_at else None,
    }


class ProposalIn(BaseModel):
    offer_package: str  # basic | local_pro | automation_pro


@app.post("/businesses/{business_id}/proposal", dependencies=[Depends(require_admin)])
def create_proposal(business_id: int, payload: ProposalIn, db: Session = Depends(get_db)):
    business = get_business(db, business_id)
    try:
        proposal = build_proposal(db, business, offer_package=payload.offer_package, actor="human:operator")
    except PricebookError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    return {"proposal_id": proposal.id, "pricebook_version": proposal.pricebook_version, "content": proposal.content}


@app.post("/jobs/expire-previews", dependencies=[Depends(require_admin)])
def job_expire_previews(db: Session = Depends(get_db)):
    count = expire_previews(db)
    db.commit()
    return {"expired": count}


# --- Outreach (human-approved, guarded) ---------------------------------------


class DraftIn(BaseModel):
    channel: str
    touch_number: int = 1
    body: str
    idempotency_key: str


@app.post("/businesses/{business_id}/messages", dependencies=[Depends(require_admin)])
def create_draft(business_id: int, payload: DraftIn, db: Session = Depends(get_db)):
    business = get_business(db, business_id)
    message = draft_message(
        db,
        business,
        channel=payload.channel,
        touch_number=payload.touch_number,
        body=payload.body,
        idempotency_key=payload.idempotency_key,
    )
    db.commit()
    return {"message_id": message.id, "status": message.status}


class ApproveIn(BaseModel):
    approver: str


@app.post("/messages/{message_id}/approve", dependencies=[Depends(require_admin)])
def approve(message_id: int, payload: ApproveIn, db: Session = Depends(get_db)):
    message = db.get(Message, message_id)
    if not message:
        raise HTTPException(status_code=404, detail="message not found")
    try:
        approve_message(db, message, payload.approver)
    except OutreachBlocked as exc:
        raise HTTPException(status_code=409, detail=exc.code)
    db.commit()
    return {"ok": True, "status": message.status}


@app.post("/messages/{message_id}/mark-sent", dependencies=[Depends(require_admin)])
def sent(message_id: int, db: Session = Depends(get_db)):
    message = db.get(Message, message_id)
    if not message:
        raise HTTPException(status_code=404, detail="message not found")
    try:
        mark_sent(db, message, actor="human:operator")
    except OutreachBlocked as exc:
        raise HTTPException(status_code=409, detail=exc.code)
    business = db.get(Business, message.business_id)
    if LeadState(business.state) == LeadState.APPROVED:
        transition(db, business, LeadState.CONTACTED, actor="system:outreach", reason=f"touch {message.touch_number}")
    db.commit()
    return {"ok": True, "status": message.status}


class SuppressIn(BaseModel):
    channel: Optional[str] = None
    reason: str  # opt_out | bounce | legal | manual


@app.post("/businesses/{business_id}/suppress", dependencies=[Depends(require_admin)])
def suppress_business(business_id: int, payload: SuppressIn, db: Session = Depends(get_db)):
    business = get_business(db, business_id)
    suppress(db, business, channel=payload.channel, reason=payload.reason, actor="human:operator")
    db.commit()
    return {"ok": True, "state": business.state}


# --- Configuration lifecycle and analytics ------------------------------------


@app.get("/config/{domain}", dependencies=[Depends(require_admin)])
def get_config(domain: str, version: Optional[str] = None):
    try:
        return {
            "active": config_loader.active_version(domain),
            "versions": config_loader.list_versions(domain),
            "config": config_loader.load(domain, version),
        }
    except config_loader.ConfigNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))


class ActivateIn(BaseModel):
    version: str


@app.post("/config/{domain}/activate", dependencies=[Depends(require_admin)])
def activate_config(domain: str, payload: ActivateIn, db: Session = Depends(get_db)):
    """Publish or rollback: point the domain at an existing immutable version."""
    try:
        config_loader.set_active(domain, payload.version)
    except config_loader.ConfigNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    record_event(
        db,
        action="config_activated",
        actor="human:operator",
        entity="config",
        payload={"domain": domain, "version": payload.version},
    )
    db.commit()
    return {"ok": True, "domain": domain, "active": payload.version}


@app.get("/analytics/kpi", dependencies=[Depends(require_admin)])
def analytics(db: Session = Depends(get_db)):
    return kpi_snapshot(db)
