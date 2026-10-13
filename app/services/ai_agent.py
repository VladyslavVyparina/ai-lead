"""Bounded AI agent helpers.

Used only for structured outputs (content packs, personalized outreach).
Never rewrites React/templates. Falls back to deterministic templates when
OPENAI_API_KEY is absent so the pipeline stays testable offline.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

import httpx

from app.config import settings
from app.core.schemas import BusinessProfile, ContentContact, ContentCTA, ContentHero, ContentPack

logger = logging.getLogger("ai_lead.agent")


def _chat(messages: list[dict], *, temperature: float = 0.4) -> Optional[str]:
    if not settings.openai_api_key:
        return None
    try:
        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={
                "model": settings.chat_model,
                "temperature": temperature,
                "response_format": {"type": "json_object"},
                "messages": messages,
            },
            timeout=45.0,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.warning("LLM call failed, using deterministic fallback: %s", exc)
        return None


def generate_content_pack(profile: BusinessProfile, theme: dict) -> tuple[ContentPack, float]:
    """Return (ContentPack, cost_units). Structured copy only."""
    services = profile.facts.get("services", {}).get("value") or []
    service_names = []
    if isinstance(services, list):
        for item in services:
            if isinstance(item, dict):
                service_names.append(item.get("name", ""))
            else:
                service_names.append(str(item))
    service_names = [s for s in service_names if s]

    contacts = profile.contacts
    prompt = {
        "business": profile.name,
        "category": profile.category,
        "city": profile.city,
        "services": service_names,
        "gaps": profile.gaps,
        "rules": [
            "Return JSON with keys: hero{headline,subheadline}, services[{name,description}], cta{label,supporting}, seo_title, seo_description",
            "European Portuguese (pt-PT)",
            "Do NOT invent testimonials, certifications, prices, or awards",
            "Do NOT invent contact details",
            "Keep services list aligned to provided names only",
        ],
    }
    raw = _chat(
        [
            {
                "role": "system",
                "content": (
                    "You write structured landing-page copy for wellness preview sites. "
                    "Output JSON only. Never invent social proof or prices."
                ),
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ]
    )
    cost = 0.15 if raw else 0.0
    data: dict = {}
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}

    hero_data = data.get("hero") or {}
    headline = hero_data.get("headline") or f"{profile.name} — bem-estar em {profile.city.title()}"
    sub = hero_data.get("subheadline") or (
        f"Massagem e cuidado personalizado no {profile.city.title()}. Pré-visualização conceptual."
    )

    mapped_services = []
    ai_services = {s.get("name"): s for s in (data.get("services") or []) if isinstance(s, dict)}
    for name in service_names:
        desc = (ai_services.get(name) or {}).get("description") or ""
        # Pull price only from evidence, never from AI
        price = None
        for item in services if isinstance(services, list) else []:
            if isinstance(item, dict) and item.get("name") == name:
                price = item.get("price")
        mapped_services.append({"name": name, "description": desc, "price": price})

    cta_data = data.get("cta") or {}
    pack = ContentPack(
        hero=ContentHero(headline=headline, subheadline=sub),
        services=mapped_services,
        social_proof=[],  # never invent
        cta=ContentCTA(
            label=cta_data.get("label") or "Pedir marcação",
            supporting=cta_data.get("supporting") or "Resposta rápida · sem compromisso",
        ),
        contact=ContentContact(
            phone=contacts.get("phone", ""),
            email=contacts.get("email", ""),
            whatsapp=contacts.get("whatsapp") or contacts.get("phone", ""),
            address=(profile.facts.get("address") or {}).get("value") or "",
            instagram=contacts.get("instagram", ""),
        ),
        theme=theme,
        seo_title=data.get("seo_title") or f"{profile.name} | {profile.city.title()}",
        seo_description=data.get("seo_description")
        or f"Pré-visualização conceptual para {profile.name} em {profile.city.title()}.",
    )
    return pack, cost


def draft_personalized_outreach(profile: BusinessProfile, *, preview_url: str = "", channel: str = "email") -> tuple[str, dict, float]:
    """Personalized outreach from research gaps — not a generic blast."""
    notes = (profile.facts.get("research_notes") or {}).get("value") or ""
    gaps = profile.gaps or []
    services = profile.facts.get("services", {}).get("value") or []
    service_hint = ""
    if isinstance(services, list) and services:
        first = services[0]
        service_hint = first.get("name") if isinstance(first, dict) else str(first)

    observation = notes or (gaps[0] if gaps else "o site podia converter melhor visitas em marcações")
    research = {
        "observation": observation,
        "gaps": gaps,
        "service_hint": service_hint,
        "has_instagram": bool(profile.contacts.get("instagram")),
        "has_website": bool(profile.audit.get("has_website")),
        "preview_url": preview_url,
    }

    prompt = {
        "business_name": profile.name,
        "city": profile.city,
        "channel": channel,
        "research": research,
        "locale": "pt-PT",
        "rules": [
            "One short email/message body",
            "Reference ONE verified observation from research",
            "One clear benefit, one soft CTA",
            "No fake urgency, no invented claims",
            "If preview_url present, mention it briefly",
            "Return JSON {body: string}",
        ],
    }
    raw = _chat(
        [
            {
                "role": "system",
                "content": (
                    "You write personalized B2B outreach for local wellness businesses in Portugal. "
                    "Ground every claim in the provided research. JSON only."
                ),
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        temperature=0.5,
    )
    cost = 0.1 if raw else 0.0
    body = None
    if raw:
        try:
            body = json.loads(raw).get("body")
        except json.JSONDecodeError:
            body = None

    if not body:
        preview_line = f"\n\nPreparei uma pré-visualização: {preview_url}" if preview_url else ""
        ig = " Vi também actividade no Instagram —" if research["has_instagram"] else ""
        body = (
            f"Olá,\n\n"
            f"Reparei que {observation} —{ig} {profile.name} podia captar mais marcações "
            f"com um percurso de reserva mais claro"
            f"{f' (ex.: {service_hint})' if service_hint else ''}.{preview_line}\n\n"
            f"Posso enviar uma auditoria breve? Basta responder a esta mensagem.\n\n"
            f"Com os melhores cumprimentos"
        )
    return body.strip(), research, cost
