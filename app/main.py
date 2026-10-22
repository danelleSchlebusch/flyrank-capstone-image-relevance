import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.api import images, jobs, posts, suggestions, usage
from app.api.deps import DbDep
from app.database import SessionLocal
from app.jobs.tagging_job import recover_interrupted
from app.logging_setup import setup_logging
from app.services.embeddings import EmbeddingError
from app.services.errors import ConflictError, NotFoundError, BudgetExceededError
from app.services.post_analysis import PostAnalysisError

setup_logging()
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # A job from a previous process can't still be running (one worker): close it.
    try:
        with SessionLocal() as db:
            log.info("startup recovery: %s", recover_interrupted(db))
    except Exception:
        log.error("Startup recovery failed (is the database reachable?)")
    yield


app = FastAPI(
    title="AI Image Understanding & Content Matching Engine",
    description="Tags images with a vision model, matches them to posts, and refuses "
                "wrong matches with an explanation.",
    lifespan=lifespan,
)
for module in (images, posts, suggestions, usage, jobs):
    app.include_router(module.router)


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": message})


@app.exception_handler(NotFoundError)
async def not_found_handler(request: Request, exc: NotFoundError):
    return _error(404, str(exc))


@app.exception_handler(ConflictError)
async def conflict_handler(request: Request, exc: ConflictError):
    return _error(409, str(exc))


@app.exception_handler(PostAnalysisError)
@app.exception_handler(EmbeddingError)
async def upstream_handler(request: Request, exc: Exception):
    # The AI provider failed even after our retries. Log it, don't leak details.
    log.error("Upstream AI failure: %s", type(exc).__name__, exc_info=exc)
    return _error(502, "The AI service failed. Please try again later.")


@app.exception_handler(OperationalError)
async def database_down_handler(request: Request, exc: OperationalError):
    log.error("Database unavailable", exc_info=exc)
    return _error(503, "The database is unavailable. Please try again later.")


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    log.error("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return _error(500, "Internal server error")

@app.exception_handler(BudgetExceededError)
async def budget_handler(request: Request, exc: BudgetExceededError):
    return _error(429, str(exc))


@app.get("/health", tags=["system"])
def health(db: DbDep):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}