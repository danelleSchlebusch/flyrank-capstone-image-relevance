import logging
import random
import time
from datetime import datetime, timezone
from decimal import Decimal

import httpx
from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import Image, ImageVector, Job
from app.services.embeddings import EmbeddingError, embed_image
from app.services.errors import ConflictError
from app.services.tagging import tag_image
from app.services.usage import spent_today
from app.services.vision import InvalidModelOutput, VisionError

log = logging.getLogger(__name__)
ACTIVE = ["queued", "running"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _alert(message: str) -> None:
    """Failure alert: an ERROR log line, plus an optional webhook."""
    log.error("ALERT %s", message)
    if settings.alert_webhook_url:
        try:
            httpx.post(settings.alert_webhook_url, json={"text": message}, timeout=5.0)
        except Exception:
            # Deliberately not logging the exception: it can contain the webhook URL.
            log.error("Could not deliver the alert webhook")


def _needs_work(retry_failed: bool):
    """Images that still need tagging, or are tagged but not yet embedded."""
    has_vector = exists().where(
        ImageVector.image_id == Image.id,
        ImageVector.model == settings.embedding_model,
    ).correlate(Image)
    statuses = ["pending", "failed"] if retry_failed else ["pending"]
    return or_(
        Image.status.in_(statuses),
        and_(Image.status.in_(["tagged", "flagged"]), ~has_vector),
    )


def recover_interrupted(db: Session) -> dict[str, int]:
    """Clean up what a crashed run leaves behind.

    Assumes ONE worker at a time. Running several jobs in parallel would need
    a heartbeat check instead.
    """
    stuck_images = db.execute(
        update(Image).where(Image.status == "processing").values(status="pending")
    ).rowcount
    stale_jobs = db.execute(
        update(Job).where(Job.status.in_(ACTIVE)).values(
            status="stopped",
            stop_reason="Interrupted: the process crashed or lost its database connection",
            finished_at=_now(),
        )
    ).rowcount
    db.commit()
    return {"images_reset": stuck_images, "jobs_closed": stale_jobs}


def create_job(db: Session, retry_failed: bool = False) -> Job:
    active = db.scalar(select(Job.id).where(Job.status.in_(ACTIVE)).limit(1))
    if active is not None:
        raise ConflictError(f"job {active} is still active; wait for it to finish")
    total = db.scalar(select(func.count()).select_from(Image).where(_needs_work(retry_failed)))
    job = Job(type="process_images", status="queued", total=total or 0)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _over_budget(db: Session) -> bool:
    limit = Decimal(str(settings.daily_budget_usd))
    return limit > 0 and spent_today(db) >= limit


def _process_one(db: Session, image: Image) -> str:
    """Tag one image with retries. Returns 'tagged', 'flagged' or 'failed'.
    Images that are already tagged pass straight through."""
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


def _embed(db: Session, image: Image) -> bool:
    try:
        embed_image(db, image)   # has its own retries and backoff
        return True
    except EmbeddingError as exc:
        db.rollback()
        # The image stays tagged; the next run notices it has no vector and tries again.
        image.last_error = f"embedding: {str(exc)[:450]}"
        db.commit()
        return False


def _record_crash(job_id: int, exc: Exception) -> None:
    """Best effort, on a fresh session, because the old one may be broken."""
    try:
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            job.status = "stopped"
            job.stop_reason = f"Crashed: {type(exc).__name__}: {str(exc)[:300]}"
            job.finished_at = _now()
            db.commit()
    except Exception:
        log.error("Could not record the crash (database unreachable); "
                  "recover_interrupted() will clean up on the next start")


def run_tagging_job(job_id: int, retry_failed: bool = False) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        job.status = "running"
        job.started_at = _now()
        db.commit()

        image_ids = list(db.scalars(
            select(Image.id).where(_needs_work(retry_failed)).order_by(Image.id)
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
                if outcome in ("tagged", "flagged") and not _embed(db, image):
                    outcome = "failed"
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
            _alert(f"job {job_id} crashed: {type(exc).__name__}")
            raise

        job.status = "stopped" if stop_reason else "completed"
        job.stop_reason = stop_reason
        job.finished_at = _now()
        db.commit()
        summary = (f"job {job.id} {job.status}: {job.succeeded} done, {job.flagged} flagged, "
                   f"{job.failed} failed" + (f" ({stop_reason})" if stop_reason else ""))
        log.info(summary)
        if job.status == "stopped" or job.failed:
            _alert(summary)