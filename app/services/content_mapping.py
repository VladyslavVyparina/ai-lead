"""Template-driven content mapping: BusinessProfile → ContentPack → SiteConfig.

AI may fill structured copy fields only. React templates stay fixed.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.core.schemas import (
    BusinessProfile,
    ContentPack,
    ServiceItem,
    SiteConfig,
    SourcedFact,
)
from app.models import Business, ContactPoint, Evidence, WebsiteAudit
from app.services.ai_agent import generate_content_pack
from app.services.config_loader import load


PLACEHOLDER_MARKERS = (
    "lorem ipsum",
    "TODO",
    "TBD",
    "placeholder",
    "your business",
    "example.com",
    "xxx",
)


def build_business_profile(db: Session, business: Business) -> BusinessProfile:
    evidence = db.query(Evidence).filter(Evidence.business_id == business.id).all()
    facts: dict = {}
    for e in evidence:
        # Prefer newest non-missing
        prev = facts.get(e.fact_key)
        if prev and prev.get("status") == "confirmed" and e.status != "confirmed":
            continue
        facts[e.fact_key] = {
            "value": e.fact_value,
            "status": e.status,
            "confidence": e.confidence,
            "source_url": e.source_url,
        }

    contacts = {
        c.channel: c.value
        for c in db.query(ContactPoint).filter(ContactPoint.business_id == business.id)
    }
    audit_row = (
        db.query(WebsiteAudit)
        .filter(WebsiteAudit.business_id == business.id)
        .order_by(WebsiteAudit.created_at.desc())
        .first()
    )
    audit = {}
    gaps: list[str] = []
    if audit_row:
        audit = {
            "has_website": audit_row.has_website,
            "mobile_ok": audit_row.mobile_ok,
            "has_booking_cta": audit_row.has_booking_cta,
            "services_clear": audit_row.services_clear,
            "contact_clear": audit_row.contact_clear,
            "notes": audit_row.notes,
        }
        if not audit_row.has_website:
            gaps.append("No website")
        elif not audit_row.mobile_ok:
            gaps.append("Outdated / non-mobile website")
        if not audit_row.has_booking_cta:
            gaps.append("No booking flow")
        if not audit_row.services_clear:
            gaps.append("Services unclear on current site")
        if not audit_row.contact_clear:
            gaps.append("Contact path unclear")
    if contacts.get("instagram"):
        gaps.append("Active Instagram")  # opportunity signal kept in research list
    # gaps used for outreach: keep digital gaps separate — filter opportunity tags later

    return BusinessProfile(
        business_id=business.id,
        name=business.name,
        category=business.category,
        city=business.city,
        website_url=business.website_url or "",
        facts=facts,
        contacts=contacts,
        audit=audit,
        gaps=gaps,
    )


def theme_tokens_for(category: str) -> dict:
    templates = load("templates")
    themes = templates.get("themes") or {}
    return dict(themes.get(category) or themes.get("default") or {
        "primary_color": "#2f6f5e",
        "accent_color": "#b08948",
        "font_display": "Fraunces",
        "font_body": "Outfit",
    })


def map_content_to_site_config(
    profile: BusinessProfile,
    pack: ContentPack,
    *,
    form_endpoint: str,
) -> SiteConfig:
    def sourced(key: str, override: Optional[str] = None) -> Optional[SourcedFact]:
        if override:
            meta = profile.facts.get(key) or {}
            return SourcedFact(
                value=override,
                source_url=meta.get("source_url", ""),
                confidence=float(meta.get("confidence") or 0.9),
            )
        meta = profile.facts.get(key)
        if not meta or meta.get("status") == "missing" or meta.get("value") in (None, ""):
            return None
        return SourcedFact(
            value=str(meta["value"]),
            source_url=meta.get("source_url", ""),
            confidence=float(meta.get("confidence") or 0.8),
        )

    services: list[ServiceItem] = []
    services_meta = profile.facts.get("services") or {}
    for item in pack.services:
        name = item.get("name")
        if not name:
            continue
        price_val = item.get("price")
        services.append(
            ServiceItem(
                name=SourcedFact(
                    value=name,
                    source_url=services_meta.get("source_url", ""),
                    confidence=float(services_meta.get("confidence") or 0.9),
                ),
                price=(
                    SourcedFact(
                        value=str(price_val),
                        source_url=services_meta.get("source_url", ""),
                        confidence=float(services_meta.get("confidence") or 0.9),
                    )
                    if price_val
                    else None
                ),
                description=item.get("description") or "",
            )
        )

    phone = pack.contact.phone or profile.contacts.get("phone", "")
    email = pack.contact.email or profile.contacts.get("email", "")
    whatsapp = pack.contact.whatsapp or phone
    socials = {}
    if pack.contact.instagram or profile.contacts.get("instagram"):
        socials["instagram"] = pack.contact.instagram or profile.contacts.get("instagram", "")

    return SiteConfig(
        business_name=SourcedFact(value=profile.name, confidence=1.0),
        category=profile.category,
        city=profile.city,
        address=sourced("address", pack.contact.address or None),
        hours=sourced("hours"),
        phone=SourcedFact(value=phone, confidence=1.0) if phone else None,
        email=SourcedFact(value=email, confidence=1.0) if email else None,
        whatsapp=SourcedFact(value=whatsapp, confidence=0.9) if whatsapp else None,
        socials=socials,
        services=services,
        description=SourcedFact(
            value=pack.hero.subheadline,
            confidence=0.85,
        ),
        primary_color=(pack.theme or {}).get("primary_color", "#2f6f5e"),
        seo_title=pack.seo_title,
        seo_description=pack.seo_description,
        booking_url=str((profile.facts.get("booking_url") or {}).get("value") or ""),
        form_endpoint=form_endpoint,
        mode="preview",
        noindex=True,
    )


def generate_mapped_content(db: Session, business: Business) -> tuple[BusinessProfile, ContentPack, SiteConfig, float]:
    from app.config import settings

    profile = build_business_profile(db, business)
    theme = theme_tokens_for(business.category)
    pack, cost = generate_content_pack(profile, theme)
    site_config = map_content_to_site_config(
        profile,
        pack,
        form_endpoint=f"{settings.public_base_url}/preview-form",
    )
    return profile, pack, site_config, cost


def content_has_placeholders(pack: ContentPack, site_config: SiteConfig) -> list[str]:
    flagged: list[str] = []
    blobs = [
        pack.hero.headline,
        pack.hero.subheadline,
        pack.cta.label,
        pack.seo_title,
        pack.seo_description,
        site_config.business_name.value,
    ]
    for svc in pack.services:
        blobs.append(svc.get("name", ""))
        blobs.append(svc.get("description", ""))
    for blob in blobs:
        low = (blob or "").lower()
        for marker in PLACEHOLDER_MARKERS:
            if marker in low:
                flagged.append(f"placeholder:{marker}")
                break
    if pack.social_proof:
        flagged.append("invented_or_unverified_social_proof")
    return flagged
