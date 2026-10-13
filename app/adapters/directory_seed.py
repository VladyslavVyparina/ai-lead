"""Directory seed connector — deterministic discovery for the pilot vertical slice.

Loads a curated catalog (config/connectors/*.yaml), emits SourceRecordIn rows,
and resumes via connector.cursor / SourceRun.checkpoint. Idempotent via
ingestion keys.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import yaml
from sqlalchemy.orm import Session

from app.config import CONFIG_DIR
from app.core.schemas import Provenance, SourceRecordIn
from app.models import SourceConnector, SourceRun
from app.services.ingestion import ingest_record

CATALOGS_DIR = CONFIG_DIR / "connectors"


def load_catalog(catalog_ref: str) -> dict:
    name = catalog_ref if catalog_ref.endswith(".yaml") else f"{catalog_ref}.yaml"
    path = CATALOGS_DIR / name
    if not path.exists():
        # allow bare id like porto_wellness_v1
        path = CATALOGS_DIR / f"{catalog_ref}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"catalog not found: {catalog_ref}")
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _entry_to_record(entry: dict, catalog: dict, connector: SourceConnector) -> SourceRecordIn:
    return SourceRecordIn(
        business_name=entry["name"],
        category=catalog.get("category") or connector.category or "massage_wellness",
        city=(catalog.get("city") or (connector.cities or ["porto"])[0]).lower(),
        address=entry.get("address", ""),
        website_url=entry.get("website_url", ""),
        profile_url=entry.get("profile_url", ""),
        email=entry.get("email", ""),
        phone=entry.get("phone", ""),
        instagram=entry.get("instagram", ""),
        extra={"catalog_id": catalog.get("catalog_id", ""), "seed": entry},
        provenance=Provenance(
            source=catalog.get("source") or connector.name,
            url=entry.get("profile_url", ""),
            permitted_use=connector.permitted_use or catalog.get("permitted_use", ""),
        ),
    )


def run_directory(
    db: Session,
    connector: SourceConnector,
    *,
    resume_run: Optional[SourceRun] = None,
    limit: Optional[int] = None,
) -> SourceRun:
    catalog_ref = connector.catalog_ref or "porto_wellness_v1"
    catalog = load_catalog(catalog_ref)
    entries: list[dict] = list(catalog.get("businesses") or [])

    run = resume_run or SourceRun(connector_id=connector.id, status="running")
    if resume_run:
        run.status = "running"
        run.error = None
    db.add(run)
    db.flush()

    start = run.checkpoint or 0
    quota = limit or connector.quota_per_run or len(entries)
    end = min(len(entries), start + quota)

    try:
        for index in range(start, end):
            entry = entries[index]
            try:
                record = _entry_to_record(entry, catalog, connector)
            except Exception:
                run.records_skipped += 1
                run.error_count += 1
                run.checkpoint = index + 1
                continue
            if not record.business_name:
                run.records_skipped += 1
                run.checkpoint = index + 1
                continue
            _, created = ingest_record(
                db, connector, record, route="discovered", source_run_id=run.id
            )
            if created:
                run.records_ingested += 1
            else:
                run.records_skipped += 1
            run.checkpoint = index + 1
            run.cursor_after = str(index + 1)

        run.status = "completed"
        run.finished_at = datetime.utcnow()
        connector.cursor = run.cursor_after
        connector.record_count = (connector.record_count or 0) + run.records_ingested
        connector.error_count = (connector.error_count or 0) + run.error_count
        connector.last_run_at = run.finished_at
        connector.next_run_at = run.finished_at + timedelta(days=7)
        db.add(connector)
    except Exception as exc:
        run.status = "failed"
        run.error = str(exc)
        run.error_count += 1
        connector.error_count = (connector.error_count or 0) + 1
        db.add(connector)

    db.add(run)
    db.commit()
    return run


def ensure_porto_wellness_connector(db: Session) -> SourceConnector:
    """Idempotently create the pilot directory connector."""
    existing = (
        db.query(SourceConnector)
        .filter(SourceConnector.name == "porto-wellness-directory")
        .first()
    )
    if existing:
        return existing
    connector = SourceConnector(
        name="porto-wellness-directory",
        adapter="directory_seed",
        owner="system",
        permitted_use="pilot research, public business data",
        cities=["porto"],
        category="massage_wellness",
        catalog_ref="porto_wellness_v1",
        quota_per_run=20,
    )
    db.add(connector)
    db.commit()
    db.refresh(connector)
    return connector
