import logging
import sys

from app.database import SessionLocal
from app.jobs.tagging_job import create_job, run_tagging_job, recover_interrupted
from app.models import Job
from app.services.ingestion import ingest_directory

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

for noisy in ("google_genai", "httpx", "google_genai.models"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

retry_failed = "--retry-failed" in sys.argv

with SessionLocal() as db:
    print("recovered:", recover_interrupted(db))
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