import logging
import sys

from app.database import SessionLocal
from app.jobs.tagging_job import create_job, run_tagging_job
from app.models import Job
from app.services.ingestion import ingest_directory

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

retry_failed = "--retry-failed" in sys.argv

with SessionLocal() as db:
    print("ingest:", ingest_directory(db))
    job = create_job(db, retry_failed=retry_failed)
    job_id = job.id
    print(f"job {job_id} created: {job.total} images to process")

run_tagging_job(job_id, retry_failed=retry_failed)

with SessionLocal() as db:
    job = db.get(Job, job_id)
    print(f"\nJob {job.id}: {job.status} | tagged={job.succeeded} "
          f"flagged={job.flagged} failed={job.failed}")
    if job.stop_reason:
        print("Stopped because:", job.stop_reason)