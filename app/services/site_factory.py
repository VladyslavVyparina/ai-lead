"""Website factory (§5.2): verified facts → ContentPack → SiteConfig → QA → deploy.

Template-driven: niche template + theme tokens + content mapping. No LLM
rewrites a repository per lead — only structured ContentPack fields change.
"""

from typing import Optional
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.core.schemas import QAResult, ServiceItem, SiteConfig, SourcedFact
from app.models import Business, ContactPoint, Evidence, WebsiteBuild
from app.services.config_loader import load
from app.services.content_mapping import (
    content_has_placeholders,
    generate_mapped_content,
)
from app.services.events import record_event
from app.services.preview_forms import form_token
from app.adapters.hosting import get_hosting_adapter

LOW_CONFIDENCE_THRESHOLD = 0.7

PIPELINE_STAGES = [
    "approved",
    "preview_generation_queued",
    "content_generated",
    "assets_generated",
    "build_started",
    "deployed",
    "validation_passed",
    "preview_ready",
]


class FactValidationError(Exception):
    """Raised when SiteConfig contains low-confidence facts; route to human review."""

    def __init__(self, flagged: list[str]):
        self.flagged = flagged
        super().__init__(f"Low-confidence facts require review: {', '.join(flagged)}")


def build_site_config(db: Session, business: Business) -> SiteConfig:
    """Assemble SiteConfig strictly from stored evidence. Missing facts are omitted."""
    evidence = {e.fact_key: e for e in db.query(Evidence).filter(Evidence.business_id == business.id)}
    contacts = {c.channel: c for c in db.query(ContactPoint).filter(ContactPoint.business_id == business.id)}

    def fact(key: str) -> Optional[SourcedFact]:
        e = evidence.get(key)
        if e is None or e.status == "missing" or e.fact_value is None:
            return None
        return SourcedFact(value=str(e.fact_value), source_url=e.source_url, confidence=e.confidence)

    def contact_fact(channel: str) -> Optional[SourcedFact]:
        c = contacts.get(channel)
        if c is None:
            return None
        return SourcedFact(value=c.value, confidence=1.0 if c.verified else 0.5)

    services: list[ServiceItem] = []
    services_evidence = evidence.get("services")
    if services_evidence and isinstance(services_evidence.fact_value, list):
        for item in services_evidence.fact_value:
            name = item.get("name") if isinstance(item, dict) else str(item)
            price = item.get("price") if isinstance(item, dict) else None
            services.append(
                ServiceItem(
                    name=SourcedFact(
                        value=name,
                        source_url=services_evidence.source_url,
                        confidence=services_evidence.confidence,
                    ),
                    price=(
                        SourcedFact(
                            value=str(price),
                            source_url=services_evidence.source_url,
                            confidence=services_evidence.confidence,
                        )
                        if price
                        else None
                    ),
                )
            )

    socials = {}
    if "instagram" in contacts:
        socials["instagram"] = contacts["instagram"].value

    return SiteConfig(
        business_name=SourcedFact(value=business.name, confidence=1.0),
        category=business.category,
        city=business.city,
        address=fact("address"),
        hours=fact("hours"),
        phone=contact_fact("phone"),
        email=contact_fact("email"),
        whatsapp=contact_fact("whatsapp") or contact_fact("phone"),
        socials=socials,
        services=services,
        description=fact("description"),
        booking_url=str(evidence["booking_url"].fact_value) if "booking_url" in evidence else "",
        form_endpoint=f"{settings.public_base_url}/preview-form",
        mode="preview",
        noindex=True,
    )


def run_qa(
    site_config: SiteConfig,
    deploy_result: dict,
    *,
    content_pack: Optional[dict] = None,
) -> QAResult:
    """Functional QA gate (§5.2) + preview automation checks."""
    notes: list[str] = []
    links_ok = bool(site_config.phone or site_config.email or site_config.whatsapp)
    if not links_ok:
        notes.append("No click-to-call/email/WhatsApp contact available")
    booking_ok = bool(site_config.booking_url) or bool(site_config.form_endpoint)
    if not booking_ok:
        notes.append("No booking link or booking-request form")
    form_ok = bool(site_config.form_endpoint)
    build_ok = deploy_result.get("status") == "deployed"

    no_placeholders = True
    brand_contacts_ok = True
    unofficial_disclaimer = True
    no_invented_social_proof = True

    if content_pack:
        from app.core.schemas import ContentPack

        pack = ContentPack.model_validate(content_pack)
        flags = content_has_placeholders(pack, site_config)
        if any(f.startswith("placeholder:") for f in flags):
            no_placeholders = False
            notes.append("Placeholder text detected")
        if "invented_or_unverified_social_proof" in flags:
            no_invented_social_proof = False
            notes.append("Unverified social proof present")
        if not (pack.disclaimer or "").lower().startswith("pré-visualização") and "conceptual" not in (
            pack.disclaimer or ""
        ).lower():
            # Accept either PT disclaimer or explicit unofficial wording
            if "não oficial" not in (pack.disclaimer or "").lower() and "unofficial" not in (
                pack.disclaimer or ""
            ).lower():
                unofficial_disclaimer = False
                notes.append("Missing unofficial concept disclaimer")
        # Brand contacts on pack must match SiteConfig when both present
        if pack.contact.phone and site_config.phone and pack.contact.phone != site_config.phone.value:
            brand_contacts_ok = False
            notes.append("Phone mismatch between content pack and site config")
        if pack.contact.email and site_config.email and pack.contact.email.lower() != site_config.email.value.lower():
            brand_contacts_ok = False
            notes.append("Email mismatch between content pack and site config")
    else:
        unofficial_disclaimer = True  # legacy path without content pack

    return QAResult(
        build_ok=build_ok,
        links_ok=links_ok,
        form_ok=form_ok,
        booking_ok=booking_ok,
        responsive_ok=build_ok,
        no_placeholders=no_placeholders,
        brand_contacts_ok=brand_contacts_ok,
        unofficial_disclaimer=unofficial_disclaimer,
        no_invented_social_proof=no_invented_social_proof,
        notes=notes,
    )


def build_and_deploy_preview(db: Session, business: Business, *, actor: str) -> WebsiteBuild:
    """Legacy-compatible entry: evidence → SiteConfig → deploy (no content pack)."""
    templates_cfg = load("templates")
    template = templates_cfg["categories"].get(business.category) or templates_cfg["categories"]["default"]

    site_config = build_site_config(db, business)
    flagged = site_config.low_confidence_facts(LOW_CONFIDENCE_THRESHOLD)
    if flagged:
        raise FactValidationError(flagged)

    build = WebsiteBuild(
        business_id=business.id,
        template_id=template["id"],
        template_version=template["version"],
        site_config=site_config.model_dump(mode="json"),
        mode="preview",
        status="building",
        pipeline_stage="build_started",
        expires_at=datetime.utcnow() + timedelta(days=templates_cfg.get("preview_expiry_days", 30)),
    )
    db.add(build)
    db.flush()

    config_payload = {
        **site_config.model_dump(mode="json"),
        "build_id": str(build.id),
        "form_token": form_token(build.id),
        "disclaimer": "Pré-visualização conceptual não oficial — não representa o negócio.",
    }
    build.site_config = config_payload

    adapter = get_hosting_adapter()
    deploy_result = adapter.deploy(
        template_id=template["id"],
        template_version=template["version"],
        site_config=config_payload,
    )

    qa = run_qa(site_config, deploy_result)
    build.qa_report = qa.model_dump()
    build.preview_url = deploy_result.get("url", "")
    build.status = "deployed" if qa.passed else "failed"
    build.pipeline_stage = "preview_ready" if qa.passed else "failed"
    db.add(build)
    record_event(
        db,
        action="website_build",
        actor=actor,
        business_id=business.id,
        entity="website_build",
        entity_id=build.id,
        payload={"status": build.status, "url": build.preview_url, "qa_passed": qa.passed},
        config_version=f"template:{template['version']}",
    )
    return build


def run_preview_pipeline(db: Session, business: Business, *, actor: str) -> WebsiteBuild:
    """Full template-driven preview pipeline with staged job states."""
    templates_cfg = load("templates")
    template = templates_cfg["categories"].get(business.category) or templates_cfg["categories"]["default"]

    build = WebsiteBuild(
        business_id=business.id,
        template_id=template["id"],
        template_version=template["version"],
        site_config={},
        content_pack={},
        mode="preview",
        status="building",
        pipeline_stage="preview_generation_queued",
        expires_at=datetime.utcnow() + timedelta(days=templates_cfg.get("preview_expiry_days", 30)),
    )
    db.add(build)
    db.flush()

    try:
        build.pipeline_stage = "content_generated"
        profile, pack, site_config, cost = generate_mapped_content(db, business)
        build.content_pack = pack.model_dump(mode="json")
        build.cost_units = (build.cost_units or 0.0) + cost

        flagged = site_config.low_confidence_facts(LOW_CONFIDENCE_THRESHOLD)
        # Phone/email at 0.5 from unverified contacts shouldn't hard-block when enrichment verified them
        # Filter: only block if confidence < threshold AND status isn't just missing
        if flagged:
            raise FactValidationError(flagged)

        build.pipeline_stage = "assets_generated"
        config_payload = {
            **site_config.model_dump(mode="json"),
            "build_id": str(build.id),
            "form_token": form_token(build.id),
            "content": pack.model_dump(mode="json"),
            "disclaimer": pack.disclaimer,
            "hero": pack.hero.model_dump(),
            "cta": pack.cta.model_dump(),
        }
        build.site_config = config_payload

        build.pipeline_stage = "build_started"
        adapter = get_hosting_adapter()
        deploy_result = adapter.deploy(
            template_id=template["id"],
            template_version=template["version"],
            site_config=config_payload,
        )
        build.pipeline_stage = "deployed"
        build.preview_url = deploy_result.get("url", "")

        qa = run_qa(site_config, deploy_result, content_pack=build.content_pack)
        build.qa_report = qa.model_dump()
        if qa.passed:
            build.pipeline_stage = "validation_passed"
            build.status = "deployed"
            build.pipeline_stage = "preview_ready"
        else:
            build.status = "failed"
            build.pipeline_stage = "failed"

        record_event(
            db,
            action="preview_pipeline",
            actor=actor,
            business_id=business.id,
            entity="website_build",
            entity_id=build.id,
            payload={
                "status": build.status,
                "stage": build.pipeline_stage,
                "url": build.preview_url,
                "qa_passed": qa.passed,
                "gaps": profile.gaps,
                "cost_units": build.cost_units,
            },
            config_version=f"template:{template['version']}",
        )
    except FactValidationError:
        build.status = "failed"
        build.pipeline_stage = "failed"
        db.add(build)
        raise
    except Exception as exc:
        build.status = "failed"
        build.pipeline_stage = "failed"
        build.qa_report = {"error": str(exc)}
        db.add(build)
        raise

    db.add(build)
    db.flush()
    return build


def expire_previews(db: Session, *, actor: str = "system:teardown") -> int:
    """Teardown expired preview deployments (§5.3, §8.2)."""
    now = datetime.utcnow()
    expired = (
        db.query(WebsiteBuild)
        .filter(WebsiteBuild.status == "deployed", WebsiteBuild.mode == "preview", WebsiteBuild.expires_at < now)
        .all()
    )
    adapter = get_hosting_adapter()
    for build in expired:
        adapter.teardown(build.preview_url)
        build.status = "expired"
        build.pipeline_stage = "expired"
        db.add(build)
        record_event(
            db,
            action="preview_expired",
            actor=actor,
            business_id=build.business_id,
            entity="website_build",
            entity_id=build.id,
        )
    return len(expired)
