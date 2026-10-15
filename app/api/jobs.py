from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Path, Query
from sqlalchemy import select

from app.api.deps import DbDep
from app.jobs.tagging_job import create_job, run_tagging_job
from app.models import Job
from app.schemas.api import JobOut
from app.services.errors import NotFoundError
from app.services.ingestion import ingest_directory

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/process-images", response_model=JobOut, status_code=202)
def start(background: BackgroundTasks, db: DbDep, retry_failed: bool = False):
    """Register any new image files, then tag and embed everything that needs it,
    in the background. Returns immediately; poll GET /jobs/{id} for progress."""
    ingest_directory(db)
    job = create_job(db, retry_failed)      # 409 if a job is already active
    background.add_task(run_tagging_job, job.id, retry_failed)
    return job


@router.get("", response_model=list[JobOut])
def list_jobs(db: DbDep, limit: Annotated[int, Query(ge=1, le=100)] = 20):
    return db.scalars(select(Job).order_by(Job.id.desc()).limit(limit)).all()


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: Annotated[int, Path(ge=1)], db: DbDep):
    job = db.get(Job, job_id)
    if job is None:
        raise NotFoundError(f"job {job_id} not found")
    return job