"""Pydantic contracts: source records, inbound leads, SiteConfig (§2.2, §5.2)."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, HttpUrl


class Provenance(BaseModel):
    source: str
    url: str = ""
    captured_at: datetime = Field(default_factory=datetime.utcnow)
    permitted_use: str


class SourceRecordIn(BaseModel):
    """Normalized output every source adapter must emit."""

    business_name: str
    category: str
    city: str
    address: str = ""
    website_url: str = ""
    profile_url: str = ""
    email: str = ""
    phone: str = ""
    instagram: str = ""
    extra: dict = Field(default_factory=dict)
    provenance: Provenance


class InboundLeadIn(BaseModel):
    """Generic inbound webhook/manual-import contract (P0). Meta Lead Ads maps to this in P1."""

    campaign: str
    business_name: str
    contact_name: str = ""
    email: str = ""
    phone: str = ""
    city: str = ""
    category: str = "unknown"
    message: str = ""
    consent_channel: Literal["email", "phone", "whatsapp", "form"] = "email"
    consent_basis: Literal["opt_in", "inbound_request"] = "inbound_request"
    source: str = "inbound_webhook"
    idempotency_key: str


# --- SiteConfig (§5.2) --------------------------------------------------------


class SourcedFact(BaseModel):
    """Each fact carries source and confidence; low confidence routes to review."""

    value: str
    source_url: str = ""
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)


class ServiceItem(BaseModel):
    name: SourcedFact
    price: Optional[SourcedFact] = None  # omitted when unverified, never invented
    description: str = ""


class SiteConfig(BaseModel):
    business_name: SourcedFact
    category: str
    city: str
    address: Optional[SourcedFact] = None
    hours: Optional[SourcedFact] = None
    phone: Optional[SourcedFact] = None
    email: Optional[SourcedFact] = None
    whatsapp: Optional[SourcedFact] = None
    socials: dict[str, str] = Field(default_factory=dict)
    services: list[ServiceItem] = Field(default_factory=list)
    description: Optional[SourcedFact] = None
    team: list[SourcedFact] = Field(default_factory=list)
    logo_url: str = ""
    primary_color: str = "#1a1a2e"
    seo_title: str = ""
    seo_description: str = ""
    booking_url: str = ""  # verified existing booking link may be used in preview
    form_endpoint: str = ""  # preview: controlled operator/test endpoint
    mode: Literal["preview", "production"] = "preview"
    noindex: bool = True  # previews are unlisted and labelled

    def low_confidence_facts(self, threshold: float = 0.7) -> list[str]:
        flagged: list[str] = []
        for key in ("business_name", "address", "hours", "phone", "email", "description"):
            fact = getattr(self, key)
            if fact is not None and fact.confidence < threshold:
                flagged.append(key)
        for item in self.services:
            if item.name.confidence < threshold:
                flagged.append(f"service:{item.name.value}")
            if item.price is not None and item.price.confidence < threshold:
                flagged.append(f"price:{item.name.value}")
        return flagged


class QAResult(BaseModel):
    build_ok: bool
    links_ok: bool
    form_ok: bool
    booking_ok: bool
    responsive_ok: bool
    no_placeholders: bool = True
    brand_contacts_ok: bool = True
    unofficial_disclaimer: bool = True
    no_invented_social_proof: bool = True
    notes: list[str] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(
            [
                self.build_ok,
                self.links_ok,
                self.form_ok,
                self.booking_ok,
                self.responsive_ok,
                self.no_placeholders,
                self.brand_contacts_ok,
                self.unofficial_disclaimer,
                self.no_invented_social_proof,
            ]
        )


# --- Template-driven content (AI emits this, never React) ---------------------


class ContentHero(BaseModel):
    headline: str
    subheadline: str = ""


class ContentCTA(BaseModel):
    label: str = "Pedir marcação"
    supporting: str = ""


class ContentContact(BaseModel):
    phone: str = ""
    email: str = ""
    whatsapp: str = ""
    address: str = ""
    instagram: str = ""


class ContentPack(BaseModel):
    """Structured site copy mapped onto a niche template. No code generation."""

    hero: ContentHero
    services: list[dict] = Field(default_factory=list)
    social_proof: list[str] = Field(default_factory=list)  # only verified; empty if none
    cta: ContentCTA = Field(default_factory=ContentCTA)
    contact: ContentContact = Field(default_factory=ContentContact)
    theme: dict = Field(default_factory=dict)
    disclaimer: str = "Pré-visualização conceptual não oficial — não representa o negócio."
    seo_title: str = ""
    seo_description: str = ""


class BusinessProfile(BaseModel):
    """Normalized profile assembled from evidence before content mapping."""

    business_id: int
    name: str
    category: str
    city: str
    website_url: str = ""
    facts: dict = Field(default_factory=dict)  # fact_key -> {value, status, confidence, source_url}
    contacts: dict = Field(default_factory=dict)
    audit: dict = Field(default_factory=dict)
    gaps: list[str] = Field(default_factory=list)
