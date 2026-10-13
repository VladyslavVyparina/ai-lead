"""Pipeline job helpers: durable status, retries, cost tracking, idempotency."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models import PipelineJob
from app.services.events import record_event


def get_or_create_job(
    db: Session,
    *,
    job_type: str,
    idempotency_key: str,
    business_id: Optional[int] = None,
    connector_id: Optional[int] = None,
    source_run_id: Optional[int] = None,
    input_payload: Optional[dict] = None,
) -> tuple[PipelineJob, bool]:
    existing = db.query(PipelineJob).filter(PipelineJob.idempotency_key == idempotency_key).first()
    if existing:
        return existing, False
    job = PipelineJob(
        job_type=job_type,
        business_id=business_id,
        connector_id=connector_id,
        source_run_id=source_run_id,
        status="queued",
        idempotency_key=idempotency_key,
        input_payload=input_payload or {},
    )
    db.add(job)
    db.flush()
    return job, True


def mark_running(db: Session, job: PipelineJob, stage: str = "") -> PipelineJob:
    job.status = "running"
    job.stage = stage or job.stage
    job.attempts = (job.attempts or 0) + 1
    job.updated_at = datetime.utcnow()
    db.add(job)
    db.flush()
    return job


def mark_succeeded(
    db: Session,
    job: PipelineJob,
    *,
    result: Optional[dict] = None,
    stage: str = "",
    cost_units: float = 0.0,
) -> PipelineJob:
    job.status = "succeeded"
    job.stage = stage or job.stage
    job.result = result or {}
    job.cost_units = (job.cost_units or 0.0) + cost_units
    job.finished_at = datetime.utcnow()
    job.updated_at = job.finished_at
    job.error = None
    db.add(job)
    record_event(
        db,
        action="pipeline_job_succeeded",
        actor="system:pipeline",
        business_id=job.business_id,
        entity="pipeline_job",
        entity_id=job.id,
        payload={"job_type": job.job_type, "stage": job.stage, "cost_units": job.cost_units},
    )
    db.flush()
    return job


def mark_failed(db: Session, job: PipelineJob, error: str, *, stage: str = "") -> PipelineJob:
    job.error = error
    job.stage = stage or job.stage
    job.updated_at = datetime.utcnow()
    if (job.attempts or 0) >= (job.max_attempts or 3):
        job.status = "failed"
        job.finished_at = job.updated_at
    else:
        job.status = "queued"  # retryable
    db.add(job)
    record_event(
        db,
        action="pipeline_job_failed",
        actor="system:pipeline",
        business_id=job.business_id,
        entity="pipeline_job",
        entity_id=job.id,
        reason=error,
        payload={"job_type": job.job_type, "attempts": job.attempts, "status": job.status},
    )
    db.flush()
    return job
