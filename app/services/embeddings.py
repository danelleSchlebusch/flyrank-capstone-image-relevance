import logging
import random
import time
from functools import lru_cache

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Image, ImageVector
from app.services.usage import record_embedding_usage
from app.services.vision import is_retryable_status

log = logging.getLogger(__name__)

DOCUMENT = "RETRIEVAL_DOCUMENT"   # what we store and search through (images)
QUERY = "RETRIEVAL_QUERY"         # what does the searching (posts)


class EmbeddingError(Exception):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


@lru_cache(maxsize=1)
def _client() -> genai.Client:
    if not settings.gemini_api_key:
        raise EmbeddingError("GEMINI_API_KEY is not set in .env", retryable=False)
    return genai.Client(api_key=settings.gemini_api_key)


def build_image_text(subject: str, category: str, caption: str, attributes: list[str]) -> str:
    """The text that represents an image in embedding space."""
    return f"{subject}. Category: {category}. {caption} Attributes: {', '.join(attributes)}."


def _embed_once(text: str, task_type: str) -> list[float]:
    client = _client()
    try:
        response = client.models.embed_content(
            model=settings.embedding_model,
            contents=text,
            config=types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=settings.embedding_dim,
            ),
        )
    except genai_errors.APIError as exc:
        code = exc.code if isinstance(exc.code, int) else 0
        raise EmbeddingError(f"Embedding call failed: {exc}",
                             retryable=is_retryable_status(code)) from exc
    except Exception as exc:
        raise EmbeddingError(f"Embedding call failed: {exc}", retryable=True) from exc

    if not response.embeddings or not response.embeddings[0].values:
        raise EmbeddingError("The API returned no embedding", retryable=False)

    values = list(response.embeddings[0].values)
    if len(values) != settings.embedding_dim:
        # Never trust the shape: a wrong-sized vector would corrupt every search.
        raise EmbeddingError(
            f"Expected {settings.embedding_dim} dimensions, got {len(values)}",
            retryable=False,
        )
    return values


def embed_text(text: str, task_type: str) -> list[float]:
    """Embed with retries and exponential backoff for temporary errors."""
    for attempt in range(1, settings.job_max_attempts + 1):
        try:
            return _embed_once(text, task_type)
        except EmbeddingError as exc:
            if not exc.retryable or attempt == settings.job_max_attempts:
                raise
            delay = settings.job_backoff_base_seconds * 2 ** (attempt - 1) + random.uniform(0, 1)
            log.warning("embedding attempt %d failed, retrying in %.1fs", attempt, delay)
            time.sleep(delay)
    raise AssertionError("unreachable")


def embed_image(db: Session, image: Image) -> bool:
    """Embed one tagged image. Returns False if it was already embedded."""
    if image.meta is None:
        raise ValueError(f"{image.filename} has no metadata yet; tag it first")

    already = db.scalar(select(ImageVector.id).where(
        ImageVector.image_id == image.id,
        ImageVector.model == settings.embedding_model,
    ))
    if already:
        return False

    m = image.meta
    text = build_image_text(m.subject, m.category, m.caption, m.attributes, m.aliases)
    vector = embed_text(text, DOCUMENT)

    db.add(ImageVector(image_id=image.id, embedding=vector, model=settings.embedding_model))
    record_embedding_usage(db, settings.embedding_model, image.id, text)
    db.commit()
    return True

def build_image_text(
    subject: str, category: str, caption: str, attributes: list[str],
    aliases: list[str] | None = None,
) -> str:
    """The text that represents an image in embedding space."""
    text = f"{subject}. Category: {category}. {caption} Attributes: {', '.join(attributes)}."
    if aliases:
        text += f" Also called: {', '.join(aliases)}."
    return text