#!/usr/bin/env python3
"""Seed a demo lead + preview build and write a real SITE_CONFIG for local Next.js.

Usage (with API DB path matching your running uvicorn):
  .venv/bin/python scripts/seed_local_preview.py

Then start the site with:
  cd site_template
  SITE_CONFIG="$(cat .local-site-config.json)" npm run dev -- --port 3100
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.state_machine import LeadState  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.migrations import backfill_normalized_contacts, migrate_sqlite  # noqa: E402
from app.models import ContactPoint, Evidence, SourceConnector, WebsiteAudit  # noqa: E402
from app.core.schemas import Provenance, SourceRecordIn  # noqa: E402
from app.services.events import transition  # noqa: E402
from app.services.ingestion import ingest_record  # noqa: E402
from app.services.scoring import score_business  # noqa: E402
from app.services.site_factory import build_and_deploy_preview  # noqa: E402


def main() -> None:
    migrate_sqlite(engine)
    backfill_normalized_contacts(engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        connector = (
            db.query(SourceConnector)
            .filter(SourceConnector.name == "local-preview-seed")
            .first()
        )
        if not connector:
            connector = SourceConnector(
                name="local-preview-seed",
                adapter="csv_import",
                owner="local",
                permitted_use="local preview testing",
            )
            db.add(connector)
            db.flush()

        record = SourceRecordIn(
            business_name="Estúdio Local Preview",
            category="massage_wellness",
            city="porto",
            email="preview@local.test",
            phone="+351910000001",
            provenance=Provenance(source="seed", url="https://example.pt", permitted_use="test"),
        )
        business, created = ingest_record(db, connector, record)
        for contact in db.query(ContactPoint).filter(ContactPoint.business_id == business.id):
            contact.verified = True
            db.add(contact)

        if not db.query(WebsiteAudit).filter(WebsiteAudit.business_id == business.id).first():
            db.add(WebsiteAudit(business_id=business.id, has_website=False, notes="seed: no site"))

        for key, value in {
            "rating": 4.8,
            "review_count": 42,
            "service_breadth": 6,
            "practitioners": 3,
            "locations": 1,
            "address": "Rua Local 1, Porto",
            "description": "Estúdio de teste local para validar o preview template.",
            "services": [
                {"name": "Massagem relax", "price": "45€"},
                {"name": "Massagem desportiva", "price": "55€"},
            ],
        }.items():
            exists = (
                db.query(Evidence)
                .filter(Evidence.business_id == business.id, Evidence.fact_key == key)
                .first()
            )
            if not exists:
                db.add(
                    Evidence(
                        business_id=business.id,
                        fact_key=key,
                        fact_value=value,
                        source_url="https://example.pt",
                        confidence=1.0,
                    )
                )

        score_business(db, business)
        if LeadState(business.state) == LeadState.NEW:
            transition(db, business, LeadState.VALIDATED, actor="system:seed", reason="seed")
        if LeadState(business.state) == LeadState.VALIDATED:
            transition(db, business, LeadState.FITQL, actor="system:seed", reason="seed")
        if LeadState(business.state) == LeadState.FITQL:
            transition(db, business, LeadState.SHORTLISTED, actor="system:seed", reason="seed")

        build = build_and_deploy_preview(db, business, actor="system:seed")
        db.commit()

        out = ROOT / "site_template" / ".local-site-config.json"
        out.write_text(json.dumps(build.site_config, ensure_ascii=False, indent=2) + "\n")
        endpoint = build.site_config.get("form_endpoint", "")
        print(f"business_id={business.id} created={created}")
        print(f"build_id={build.id} status={build.status}")
        print(f"form_endpoint={endpoint}")
        print(f"wrote {out}")
        print("IMPORTANT: submissions land only in the DB of that form_endpoint.")
        print("  Watch the SAME backend in /ui (local vs VPS are different databases).")
        print("Start the site with:")
        print(f'  cd site_template && SITE_CONFIG="$(cat .local-site-config.json)" npm run dev -- --port 3100')
        print("Open preview at http://127.0.0.1:3100 (CORS allows both localhost and 127.0.0.1).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
