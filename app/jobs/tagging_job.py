import logging
import random
import time
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import Image, Job
from app.services.tagging import tag_image
from app.services.usage import spent_today
from app.services.vision import InvalidModelOutput, VisionError

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _statuses(retry_failed: bool) -> list[str]:
    return ["pending", "failed"] if retry_failed else ["pending"]

def recover_interrupted(db: Session) -> dict[str, int]:
    """Clean up what a crashed run leaves behind.

    Assumes ONE worker at a time. If we ever run several jobs in parallel,
    this must be replaced with a heartbeat check.
    """
    stuck_images = db.execute(
        update(Image).where(Image.status == "processing").values(status="pending")
    ).rowcount
    stale_jobs = db.execute(
        update(Job).where(Job.status == "running").values(
            status="stopped",
            stop_reason="Interrupted: the process crashed or lost its database connection",
            finished_at=_now(),
        )
    ).rowcount
    db.commit()
    return {"images_reset": stuck_images, "jobs_closed": stale_jobs}


def _record_crash(job_id: int, exc: Exception) -> None:
    """Best effort: use a fresh session, because the old one may be broken."""
    try:
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            job.status = "stopped"
            job.stop_reason = f"Crashed: {type(exc).__name__}: {str(exc)[:300]}"
            job.finished_at = _now()
            db.commit()
    except Exception:
        log.error("Could not record the crash (database unreachable); "
                  "recover_interrupted() will clean up on the next run")


def create_job(db: Session, retry_failed: bool = False) -> Job:
    total = db.scalar(
        select(func.count()).select_from(Image).where(Image.status.in_(_statuses(retry_failed)))
    )
    job = Job(type="tag_images", status="queued", total=total or 0)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _over_budget(db: Session) -> bool:
    limit = Decimal(str(settings.daily_budget_usd))
    return limit > 0 and spent_today(db) >= limit


def _process_one(db: Session, image: Image) -> str:
    """Tag one image with retries. Returns 'tagged', 'flagged' or 'failed'."""
    last_error = ""
    for attempt in range(1, settings.job_max_attempts + 1):
        image.attempts = attempt
        try:
            tag_image(db, image)
            image.last_error = None
            db.commit()
            return image.status
        except InvalidModelOutput as exc:
            last_error = str(exc)[:500]
            break  # same image, same prompt: retrying is unlikely to help
        except VisionError as exc:
            last_error = str(exc)[:500]
            if not exc.retryable:
                break
            if attempt < settings.job_max_attempts:
                delay = settings.job_backoff_base_seconds * 2 ** (attempt - 1)
                delay += random.uniform(0, 1)
                log.warning("%s: attempt %d failed, retrying in %.1fs",
                            image.filename, attempt, delay)
                time.sleep(delay)

    image.status = "failed"
    image.last_error = last_error
    db.commit()
    return "failed"


def run_tagging_job(job_id: int, retry_failed: bool = False) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        job.status = "running"
        job.started_at = _now()
        db.commit()

        image_ids = list(db.scalars(
            select(Image.id).where(Image.status.in_(_statuses(retry_failed))).order_by(Image.id)
        ))

        stop_reason = None
        consecutive_failures = 0

        try:
            for position, image_id in enumerate(image_ids, start=1):
                if _over_budget(db):
                    stop_reason = f"Daily budget of ${settings.daily_budget_usd} reached"
                    break
                if consecutive_failures >= settings.job_max_consecutive_failures:
                    stop_reason = (f"{consecutive_failures} images failed in a row; "
                                   "stopping to avoid wasting calls")
                    break

                image = db.get(Image, image_id)
                outcome = _process_one(db, image)
                log.info("[%d/%d] %s -> %s", position, len(image_ids), image.filename, outcome)

                if outcome == "failed":
                    job.failed += 1
                    consecutive_failures += 1
                else:
                    consecutive_failures = 0
                    if outcome == "flagged":
                        job.flagged += 1
                    else:
                        job.succeeded += 1
                db.commit()
                time.sleep(settings.job_delay_seconds)
        except Exception as exc:
            log.exception("Job %d crashed", job_id)
            db.rollback()
            _record_crash(job_id, exc)
            raise

        job.status = "stopped" if stop_reason else "completed"
        job.stop_reason = stop_reason
        job.finished_at = _now()
        db.commit()
        log.info("Job %d %s: %d tagged, %d flagged, %d failed%s", job.id, job.status,
                 job.succeeded, job.flagged, job.failed,
                 f" ({stop_reason})" if stop_reason else "")