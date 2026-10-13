"""Operator UI: server-rendered pages so a non-technical owner can run the
whole pilot loop (sources → leads → assets → outreach) without Swagger."""

from typing import Optional
import hashlib
import hmac
import secrets
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT, settings
from app.core.schemas import Provenance, SourceRecordIn
from app.core.state_machine import InvalidTransition, LeadState, TRANSITIONS
from app.database import get_db
from app.models import (
    Business,
    ContactPoint,
    Evidence,
    Message,
    PipelineJob,
    PreviewSubmission,
    Proposal,
    Qualification,
    SourceConnector,
    WebsiteAudit,
    WebsiteBuild,
)
from app.adapters.csv_import import run_import
from app.adapters.directory_seed import ensure_porto_wellness_connector
from app.services.analytics import kpi_snapshot
from app.services.events import record_event, transition
from app.services.ingestion import ingest_record
from app.services.outreach import (
    OutreachBlocked,
    approve_message,
    draft_message,
    draft_personalized_message,
    mark_sent,
    suppress,
)
from app.services.pipeline import approve_and_generate_preview, discover_enrich_score, run_porto_wellness_vertical_slice
from app.services.proposal import PricebookError, build_proposal
from app.services.scoring import assign_tiers, score_business
from app.services.site_factory import FactValidationError, build_and_deploy_preview
from app.services.enrichment import fact_status_summary

router = APIRouter()
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))

COOKIE_NAME = "ui_token"
CATEGORIES = ["massage_wellness", "beauty_aesthetics", "aesthetic_clinic", "coach_trainer", "unknown"]
CITIES = ["porto", "lisbon", "braga"]


def _authed(request: Request) -> bool:
    return secrets.compare_digest(request.cookies.get(COOKIE_NAME, ""), _session_token())


def _session_token() -> str:
    """Keep the raw ADMIN_TOKEN out of browser cookies."""
    return hmac.new(
        settings.admin_token.encode(),
        b"operator-ui-session",
        hashlib.sha256,
    ).hexdigest()


def require_ui(request: Request) -> None:
    if not _authed(request):
        raise HTTPException(status_code=303, headers={"Location": "/ui/login"})


def render(request: Request, template: str, section: str, **context) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        template,
        {"section": section, "authed": _authed(request), **context},
    )


def redirect(path: str, msg: str = "", err: str = "") -> RedirectResponse:
    if msg:
        path += ("&" if "?" in path else "?") + "msg=" + quote(msg)
    if err:
        path += ("&" if "?" in path else "?") + "err=" + quote(err)
    return RedirectResponse(path, status_code=303)


# --- Auth ----------------------------------------------------------------------


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return render(request, "login.html", section="login")


@router.post("/login")
def login(request: Request, token: str = Form(...)):
    if not secrets.compare_digest(token, settings.admin_token):
        return redirect("/ui/login", err="Невірний токен")
    response = redirect("/ui")
    response.set_cookie(
        COOKIE_NAME,
        _session_token(),
        httponly=True,
        # Bind to the actual request scheme so HTTP VPS logins work even if
        # PUBLIC_BASE_URL was set to https by mistake (or the reverse).
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=60 * 60 * 24 * 14,
    )
    return response


@router.get("/logout")
def logout():
    response = redirect("/ui/login")
    response.delete_cookie(COOKIE_NAME)
    return response


# --- Dashboard -------------------------------------------------------------------


@router.get("", response_class=HTMLResponse, dependencies=[Depends(require_ui)])
def dashboard(request: Request, db: Session = Depends(get_db)):
    submissions = (
        db.query(PreviewSubmission)
        .order_by(PreviewSubmission.created_at.desc())
        .limit(20)
        .all()
    )
    businesses = {
        b.id: b
        for b in db.query(Business).filter(
            Business.id.in_([s.business_id for s in submissions] or [0])
        )
    }
    return render(
        request,
        "dashboard.html",
        section="dashboard",
        kpi=kpi_snapshot(db),
        submissions=submissions,
        businesses=businesses,
    )


# --- Sources ---------------------------------------------------------------------


@router.get("/sources", response_class=HTMLResponse, dependencies=[Depends(require_ui)])
def sources_page(request: Request, db: Session = Depends(get_db)):
    connectors = db.query(SourceConnector).order_by(SourceConnector.id).all()
    import_connectors = [c for c in connectors if c.adapter in {"csv_import", "manual"}]
    jobs = db.query(PipelineJob).order_by(PipelineJob.id.desc()).limit(10).all()
    return render(
        request,
        "sources.html",
        section="sources",
        connectors=connectors,
        import_connectors=import_connectors,
        categories=CATEGORIES,
        cities=CITIES,
        jobs=jobs,
    )


@router.post("/sources/connectors", dependencies=[Depends(require_ui)])
def ui_create_connector(
    name: str = Form(...),
    adapter: str = Form(...),
    permitted_use: str = Form(...),
    owner: str = Form(default="owner"),
    category: str = Form(default=""),
    catalog_ref: str = Form(default=""),
    db: Session = Depends(get_db),
):
    connector = SourceConnector(
        name=name.strip(),
        adapter=adapter,
        owner=owner,
        permitted_use=permitted_use,
        category=category,
        catalog_ref=catalog_ref,
        cities=["porto"] if adapter == "directory_seed" else [],
        quota_per_run=20 if adapter == "directory_seed" else 500,
    )
    db.add(connector)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return redirect("/ui/sources", err=f"Конектор з іменем «{name}» вже існує")
    return redirect("/ui/sources", msg=f"Конектор «{name}» створено")


@router.post("/sources/porto-wellness/ensure", dependencies=[Depends(require_ui)])
def ui_ensure_porto(db: Session = Depends(get_db)):
    connector = ensure_porto_wellness_connector(db)
    return redirect("/ui/sources", msg=f"Конектор готовий: {connector.name} (#{connector.id})")


@router.post("/sources/{connector_id}/run", dependencies=[Depends(require_ui)])
def ui_run_connector(connector_id: int, db: Session = Depends(get_db)):
    connector = db.get(SourceConnector, connector_id)
    if not connector:
        return redirect("/ui/sources", err="Конектор не знайдено")
    if connector.adapter != "directory_seed":
        return redirect("/ui/sources", err="Run automation лише для directory_seed")
    try:
        result = discover_enrich_score(db, connector, limit=20, top_review=5)
    except Exception as exc:
        return redirect("/ui/sources", err=str(exc))
    queue = result.get("review_queue") or []
    return redirect(
        "/ui/leads?state=shortlisted",
        msg=(
            f"Pipeline: ingested {result.get('ingested', 0)}, enriched {result.get('enriched', 0)}, "
            f"review queue: {len(queue)} лідів (затвердіть превʼю на картці)"
        ),
    )


@router.post("/sources/porto-wellness/run", dependencies=[Depends(require_ui)])
def ui_run_porto_slice(db: Session = Depends(get_db)):
    try:
        result = run_porto_wellness_vertical_slice(db, limit=20, top_review=5)
    except Exception as exc:
        return redirect("/ui/sources", err=str(exc))
    queue = result.get("review_queue") or []
    return redirect(
        "/ui/leads?state=shortlisted",
        msg=f"Porto wellness slice: {len(queue)} лідів на approval preview",
    )


@router.post("/sources/{connector_id}/import-csv", dependencies=[Depends(require_ui)])
async def ui_import_csv(connector_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    connector = db.get(SourceConnector, connector_id)
    if not connector:
        return redirect("/ui/sources", err="Конектор не знайдено")
    try:
        csv_text = (await file.read()).decode("utf-8-sig")
    except UnicodeDecodeError:
        return redirect("/ui/sources", err="Файл не в UTF-8. Експортуйте CSV як UTF-8 і спробуйте знову")
    run = run_import(db, connector, csv_text)
    if run.status == "completed":
        return redirect(
            "/ui/leads",
            msg=f"Імпортовано {run.records_ingested} записів (пропущено {run.records_skipped})",
        )
    return redirect("/ui/sources", err=f"Імпорт зупинився на рядку {run.checkpoint}: {run.error}")


@router.post("/sources/quick-add", dependencies=[Depends(require_ui)])
def ui_quick_add(
    connector_id: int = Form(...),
    business_name: str = Form(...),
    category: str = Form(...),
    city: str = Form(...),
    profile_url: str = Form(default=""),
    website_url: str = Form(default=""),
    email: str = Form(default=""),
    phone: str = Form(default=""),
    instagram: str = Form(default=""),
    db: Session = Depends(get_db),
):
    connector = db.get(SourceConnector, connector_id)
    if not connector:
        return redirect("/ui/sources", err="Оберіть конектор")
    record = SourceRecordIn(
        business_name=business_name,
        category=category,
        city=city,
        profile_url=profile_url.strip(),
        website_url=website_url.strip(),
        email=email,
        phone=phone,
        instagram=instagram,
        provenance=Provenance(
            source=connector.name,
            url=profile_url.strip(),
            permitted_use=connector.permitted_use,
        ),
    )
    business, created = ingest_record(db, connector, record, route="discovered")
    db.commit()
    if created:
        return redirect(f"/ui/leads/{business.id}", msg="Бізнес додано")
    return redirect(f"/ui/leads/{business.id}", msg="Такий бізнес вже є — відкрито наявну картку")


# --- Leads -------------------------------------------------------------------------


@router.get("/leads", response_class=HTMLResponse, dependencies=[Depends(require_ui)])
def leads_page(
    request: Request,
    state: Optional[str] = None,
    city: Optional[str] = None,
    page: int = 1,
    db: Session = Depends(get_db),
):
    per_page = 25
    query = db.query(Business)
    if state:
        query = query.filter(Business.state == state)
    if city:
        query = query.filter(Business.city == city)
    total = query.count()
    leads = query.order_by(Business.id.desc()).offset((page - 1) * per_page).limit(per_page).all()
    quals = {
        q.business_id: q
        for q in db.query(Qualification).order_by(Qualification.created_at.asc()).all()
    }
    return render(
        request,
        "leads.html",
        section="leads",
        leads=leads,
        quals=quals,
        total=total,
        page=page,
        pages=max(1, -(-total // per_page)),
        state=state or "",
        city=city or "",
        states=[s.value for s in LeadState],
        cities=CITIES,
    )


@router.post("/leads/assign-tiers", dependencies=[Depends(require_ui)])
def ui_assign_tiers(db: Session = Depends(get_db)):
    result = assign_tiers(db)
    db.commit()
    return redirect("/ui/leads", msg=f"Tier A: {len(result['A'])}, Tier B: {len(result['B'])}")


@router.get("/leads/{business_id}", response_class=HTMLResponse, dependencies=[Depends(require_ui)])
def lead_detail(request: Request, business_id: int, db: Session = Depends(get_db)):
    business = db.get(Business, business_id)
    if not business:
        raise HTTPException(status_code=404)
    contacts = db.query(ContactPoint).filter(ContactPoint.business_id == business_id).all()
    evidence = db.query(Evidence).filter(Evidence.business_id == business_id).all()
    audit = (
        db.query(WebsiteAudit)
        .filter(WebsiteAudit.business_id == business_id)
        .order_by(WebsiteAudit.created_at.desc())
        .first()
    )
    qualification = (
        db.query(Qualification)
        .filter(Qualification.business_id == business_id)
        .order_by(Qualification.created_at.desc())
        .first()
    )
    builds = (
        db.query(WebsiteBuild)
        .filter(WebsiteBuild.business_id == business_id)
        .order_by(WebsiteBuild.created_at.desc())
        .all()
    )
    proposals = (
        db.query(Proposal)
        .filter(Proposal.business_id == business_id)
        .order_by(Proposal.created_at.desc())
        .all()
    )
    messages = (
        db.query(Message)
        .filter(Message.business_id == business_id)
        .order_by(Message.created_at.desc())
        .all()
    )
    current = LeadState(business.state)
    next_states = sorted(s.value for s in TRANSITIONS.get(current, set()))
    fact_summary = fact_status_summary(db, business)
    return render(
        request,
        "lead_detail.html",
        section="leads",
        b=business,
        contacts=contacts,
        evidence=evidence,
        audit=audit,
        qualification=qualification,
        builds=builds,
        proposals=proposals,
        messages=messages,
        next_states=next_states,
        fact_summary=fact_summary,
    )


@router.post("/leads/{business_id}/contacts/{contact_id}/verify", dependencies=[Depends(require_ui)])
def ui_verify_contact(business_id: int, contact_id: int, db: Session = Depends(get_db)):
    contact = db.get(ContactPoint, contact_id)
    if not contact or contact.business_id != business_id:
        return redirect(f"/ui/leads/{business_id}", err="Контакт не знайдено")
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
    return redirect(f"/ui/leads/{business_id}", msg=f"Контакт {contact.channel} верифіковано")


@router.post("/leads/{business_id}/audit", dependencies=[Depends(require_ui)])
def ui_save_audit(
    business_id: int,
    has_website: Optional[str] = Form(default=None),
    mobile_ok: Optional[str] = Form(default=None),
    has_booking_cta: Optional[str] = Form(default=None),
    services_clear: Optional[str] = Form(default=None),
    contact_clear: Optional[str] = Form(default=None),
    notes: str = Form(default=""),
    db: Session = Depends(get_db),
):
    db.add(
        WebsiteAudit(
            business_id=business_id,
            has_website=bool(has_website),
            mobile_ok=bool(mobile_ok),
            has_booking_cta=bool(has_booking_cta),
            services_clear=bool(services_clear),
            contact_clear=bool(contact_clear),
            notes=notes,
        )
    )
    db.commit()
    return redirect(f"/ui/leads/{business_id}", msg="Аудит збережено")


@router.post("/leads/{business_id}/evidence", dependencies=[Depends(require_ui)])
def ui_add_evidence(
    business_id: int,
    fact_key: str = Form(...),
    fact_value: str = Form(...),
    source_url: str = Form(default=""),
    confidence: float = Form(default=1.0),
    db: Session = Depends(get_db),
):
    db.add(
        Evidence(
            business_id=business_id,
            fact_key=fact_key.strip(),
            fact_value=fact_value.strip(),
            source_url=source_url.strip(),
            confidence=confidence,
        )
    )
    db.commit()
    return redirect(f"/ui/leads/{business_id}", msg=f"Факт «{fact_key}» додано")


@router.post("/leads/{business_id}/score", dependencies=[Depends(require_ui)])
def ui_score(business_id: int, db: Session = Depends(get_db)):
    business = db.get(Business, business_id)
    qualification = score_business(db, business)
    if LeadState(business.state) == LeadState.NEW:
        transition(db, business, LeadState.VALIDATED, actor="system:scoring", reason="scored")
    if LeadState(business.state) == LeadState.VALIDATED:
        target = LeadState.FITQL if qualification.routing != "reject" else LeadState.REJECTED
        transition(db, business, target, actor="system:scoring", reason=f"total={qualification.total}")
    db.commit()
    return redirect(
        f"/ui/leads/{business_id}",
        msg=f"Оцінка: {qualification.total} ({qualification.routing})",
    )


@router.post("/leads/{business_id}/transition", dependencies=[Depends(require_ui)])
def ui_transition(business_id: int, target: str = Form(...), db: Session = Depends(get_db)):
    business = db.get(Business, business_id)
    try:
        transition(db, business, LeadState(target), actor="human:operator", reason="UI action")
    except (InvalidTransition, ValueError) as exc:
        return redirect(f"/ui/leads/{business_id}", err=str(exc))
    db.commit()
    return redirect(f"/ui/leads/{business_id}", msg=f"Стан змінено на {target}")


@router.post("/leads/{business_id}/approve-preview", dependencies=[Depends(require_ui)])
def ui_approve_preview(business_id: int, db: Session = Depends(get_db)):
    business = db.get(Business, business_id)
    if not business:
        return redirect("/ui/leads", err="Лід не знайдено")
    try:
        result = approve_and_generate_preview(db, business, actor="human:operator")
    except FactValidationError as exc:
        return redirect(
            f"/ui/leads/{business_id}",
            err="Недостатньо впевненості у фактах: " + ", ".join(exc.flagged),
        )
    except ValueError as exc:
        return redirect(f"/ui/leads/{business_id}", err=str(exc))
    except Exception as exc:
        return redirect(f"/ui/leads/{business_id}", err=str(exc))
    if result.get("status") == "deployed":
        return redirect(
            f"/ui/leads/{business_id}",
            msg=f"Preview ready: {result.get('preview_url')} · outreach draft #{result.get('message_id')}",
        )
    return redirect(f"/ui/leads/{business_id}", err="Preview pipeline failed QA — див. білд")


@router.post("/leads/{business_id}/build-preview", dependencies=[Depends(require_ui)])
def ui_build_preview(business_id: int, db: Session = Depends(get_db)):
    business = db.get(Business, business_id)
    try:
        build = build_and_deploy_preview(db, business, actor="human:operator")
    except FactValidationError as exc:
        return redirect(
            f"/ui/leads/{business_id}",
            err="Недостатньо впевненості у фактах: " + ", ".join(exc.flagged) + ". Верифікуйте контакти/факти.",
        )
    if build.status == "deployed" and LeadState(business.state) == LeadState.SHORTLISTED:
        transition(db, business, LeadState.PREVIEW_READY, actor="system:site_factory", reason=build.preview_url)
    db.commit()
    if build.status == "deployed":
        return redirect(f"/ui/leads/{business_id}", msg=f"Превʼю розгорнуто: {build.preview_url}")
    return redirect(f"/ui/leads/{business_id}", err="Білд не пройшов QA — див. картку білда")


@router.post("/leads/{business_id}/outreach-draft", dependencies=[Depends(require_ui)])
def ui_personalized_draft(business_id: int, db: Session = Depends(get_db)):
    business = db.get(Business, business_id)
    if not business:
        return redirect("/ui/leads", err="Лід не знайдено")
    latest_build = (
        db.query(WebsiteBuild)
        .filter(WebsiteBuild.business_id == business.id, WebsiteBuild.status == "deployed")
        .order_by(WebsiteBuild.created_at.desc())
        .first()
    )
    message = draft_personalized_message(
        db,
        business,
        channel="email",
        touch_number=1,
        preview_url=latest_build.preview_url if latest_build else "",
    )
    db.commit()
    return redirect("/ui/outreach", msg=f"Персоналізовану чернетку #{message.id} створено з ресерчу")


@router.post("/leads/{business_id}/proposal", dependencies=[Depends(require_ui)])
def ui_create_proposal(business_id: int, offer_package: str = Form(...), db: Session = Depends(get_db)):
    business = db.get(Business, business_id)
    try:
        build_proposal(db, business, offer_package=offer_package, actor="human:operator")
    except PricebookError as exc:
        return redirect(f"/ui/leads/{business_id}", err=str(exc))
    db.commit()
    return redirect(f"/ui/leads/{business_id}", msg=f"Пропозицію «{offer_package}» створено")


@router.post("/leads/{business_id}/suppress", dependencies=[Depends(require_ui)])
def ui_suppress(business_id: int, reason: str = Form(default="opt_out"), db: Session = Depends(get_db)):
    business = db.get(Business, business_id)
    suppress(db, business, channel=None, reason=reason, actor="human:operator")
    db.commit()
    return redirect(f"/ui/leads/{business_id}", msg="Лід заблоковано для контактів (suppressed)")


# --- Outreach ------------------------------------------------------------------------


@router.get("/outreach", response_class=HTMLResponse, dependencies=[Depends(require_ui)])
def outreach_page(request: Request, db: Session = Depends(get_db)):
    messages = db.query(Message).order_by(Message.created_at.desc()).limit(200).all()
    businesses = {
        b.id: b
        for b in db.query(Business).filter(Business.id.in_([m.business_id for m in messages] or [0]))
    }
    contactable = (
        db.query(Business)
        .filter(Business.state.in_(["preview_ready", "approved", "contacted", "follow_up", "engaged"]))
        .order_by(Business.id.desc())
        .all()
    )
    return render(
        request,
        "outreach.html",
        section="outreach",
        messages=messages,
        businesses=businesses,
        contactable=contactable,
    )


@router.post("/outreach/draft", dependencies=[Depends(require_ui)])
def ui_draft_message(
    business_id: int = Form(...),
    channel: str = Form(...),
    touch_number: int = Form(default=1),
    body: str = Form(...),
    db: Session = Depends(get_db),
):
    business = db.get(Business, business_id)
    if not business:
        return redirect("/ui/outreach", err="Бізнес не знайдено")
    message = draft_message(
        db,
        business,
        channel=channel,
        touch_number=touch_number,
        body=body,
        idempotency_key=f"ui:{business_id}:{channel}:{touch_number}",
    )
    db.commit()
    return redirect("/ui/outreach", msg=f"Чернетку #{message.id} створено")


@router.post("/outreach/{message_id}/approve", dependencies=[Depends(require_ui)])
def ui_approve_message(message_id: int, db: Session = Depends(get_db)):
    message = db.get(Message, message_id)
    if not message:
        return redirect("/ui/outreach", err="Повідомлення не знайдено")
    try:
        approve_message(db, message, approver="operator")
    except OutreachBlocked as exc:
        return redirect("/ui/outreach", err=f"Заблоковано: {exc.code}")
    db.commit()
    return redirect("/ui/outreach", msg=f"Повідомлення #{message_id} затверджено")


@router.post("/outreach/{message_id}/mark-sent", dependencies=[Depends(require_ui)])
def ui_mark_sent(message_id: int, db: Session = Depends(get_db)):
    message = db.get(Message, message_id)
    if not message:
        return redirect("/ui/outreach", err="Повідомлення не знайдено")
    try:
        mark_sent(db, message, actor="human:operator")
    except OutreachBlocked as exc:
        return redirect("/ui/outreach", err=f"Заблоковано: {exc.code}")
    business = db.get(Business, message.business_id)
    if LeadState(business.state) == LeadState.APPROVED:
        transition(db, business, LeadState.CONTACTED, actor="system:outreach", reason=f"touch {message.touch_number}")
    db.commit()
    return redirect("/ui/outreach", msg=f"Повідомлення #{message_id} позначено відправленим")
