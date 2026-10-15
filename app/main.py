import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import images, posts
from app.api.deps import DbDep
from app.services.embeddings import EmbeddingError
from app.services.errors import ConflictError, NotFoundError
from app.services.post_analysis import PostAnalysisError

log = logging.getLogger(__name__)

app = FastAPI(
    title="AI Image Understanding & Content Matching Engine",
    description="Tags images with a vision model, matches them to posts, and refuses "
                "wrong matches with an explanation.",
)
app.include_router(images.router)
app.include_router(posts.router)


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
    # The AI provider failed after our retries. Log the detail, but don't send it to the client.
    log.error("Upstream AI failure: %s", type(exc).__name__, exc_info=exc)
    return _error(502, "The AI service failed. Please try again later.")


@app.get("/health", tags=["system"])
def health(db: DbDep):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}