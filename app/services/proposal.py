"""Proposal factory (§5.4): ProposalTemplate + verified facts/audit + Preview URL
+ eligible Offer + Pricebook → versioned proposal. No prices in code (§2.1)."""

from typing import Optional
from sqlalchemy.orm import Session

from app.models import Business, Evidence, Proposal, WebsiteAudit, WebsiteBuild
from app.services.config_loader import load
from app.services.events import record_event


class PricebookError(Exception):
    pass


def build_proposal(
    db: Session,
    business: Business,
    *,
    offer_package: str,
    actor: str,
    pricebook_version: Optional[str] = None,
) -> Proposal:
    pricebook = load("pricebook", pricebook_version)
    packages = pricebook["packages"]
    if offer_package not in packages:
        raise PricebookError(f"Unknown package: {offer_package}")
    package = packages[offer_package]

    audit = (
        db.query(WebsiteAudit)
        .filter(WebsiteAudit.business_id == business.id)
        .order_by(WebsiteAudit.created_at.desc())
        .first()
    )
    build = (
        db.query(WebsiteBuild)
        .filter(WebsiteBuild.business_id == business.id, WebsiteBuild.status == "deployed")
        .order_by(WebsiteBuild.created_at.desc())
        .first()
    )
    evidence_refs = [
        {"fact": e.fact_key, "source": e.source_url, "confidence": e.confidence}
        for e in db.query(Evidence).filter(Evidence.business_id == business.id)
    ]

    content = {
        "business": business.name,
        "city": business.city,
        "audit": (
            {
                "has_website": audit.has_website,
                "mobile_ok": audit.mobile_ok,
                "has_booking_cta": audit.has_booking_cta,
                "notes": audit.notes,
            }
            if audit
            else None
        ),
        "preview_url": build.preview_url if build else None,
        "offer": {
            "package": offer_package,
            "price_from_eur": package["price_from_eur"],
            "price_to_eur": package.get("price_to_eur"),
            "scope": package["scope"],
        },
        "support_plans": pricebook["support_plans"],
        "payment_terms": pricebook["payment_terms"],
        "evidence": evidence_refs,
    }

    proposal = Proposal(
        business_id=business.id,
        template_version=pricebook.get("proposal_template_version", "kaapora-v1"),
        pricebook_version=pricebook["_version"],
        offer_package=offer_package,
        content=content,
    )
    db.add(proposal)
    db.flush()
    record_event(
        db,
        action="proposal_created",
        actor=actor,
        business_id=business.id,
        entity="proposal",
        entity_id=proposal.id,
        config_version=pricebook["_version"],
    )
    return proposal
