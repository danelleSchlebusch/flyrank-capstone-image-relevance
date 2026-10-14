from dataclasses import dataclass
from functools import lru_cache

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import ValidationError

from app.config import settings
from app.schemas.post import PostSubject
from app.services.retry import call_with_backoff
from app.services.vision import is_retryable_status

PROMPT = """You read a blog post and identify what it is mainly about, so that a
matching image can be checked against it.

Rules:
- subject: the single main physical subject of the post, as a common English name,
  singular and lowercase (e.g. "fox", "gray wolf", "sailboat"). If the post names a
  specific kind (e.g. "red foxes"), keep it ("red fox"); otherwise use the general
  name. Never use Latin names. If the post is not mainly about one concrete, visible
  subject (opinion, advice, abstract ideas), return an empty string.
- category: exactly one of: animal, vehicle, building, furniture, electronics, other,
  unclear. Use "unclear" when the subject is empty.
"""


class PostAnalysisError(Exception):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class PostAnalysis:
    tags: PostSubject
    model: str
    input_tokens: int
    output_tokens: int


@lru_cache(maxsize=1)
def _client() -> genai.Client:
    if not settings.gemini_api_key:
        raise PostAnalysisError("GEMINI_API_KEY is not set in .env")
    return genai.Client(api_key=settings.gemini_api_key)


def analyze_post(title: str, content: str) -> PostAnalysis:
    client = _client()

    def call():
        try:
            return client.models.generate_content(
                model=settings.text_model,
                contents=f"{PROMPT}\n\nTitle: {title}\n\nPost:\n{content}",
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=PostSubject,
                    temperature=0,
                ),
            )
        except genai_errors.APIError as exc:
            code = exc.code if isinstance(exc.code, int) else 0
            raise PostAnalysisError(f"Gemini call failed: {exc}",
                                    retryable=is_retryable_status(code)) from exc
        except Exception as exc:
            raise PostAnalysisError(f"Gemini call failed: {exc}", retryable=True) from exc

    response = call_with_backoff(call, label="post analysis")

    try:
        tags = PostSubject.model_validate_json(response.text or "")
    except ValidationError as exc:
        raise PostAnalysisError(f"Response failed schema validation: {exc}") from exc

    usage = response.usage_metadata
    return PostAnalysis(
        tags=tags,
        model=settings.text_model,
        input_tokens=(usage.prompt_token_count or 0) if usage else 0,
        output_tokens=(
            ((usage.candidates_token_count or 0) + (getattr(usage, "thoughts_token_count", 0) or 0))
            if usage else 0
        ),
    )