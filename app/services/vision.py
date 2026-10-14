import mimetypes
from dataclasses import dataclass
from pathlib import Path

from google import genai
from google.genai import types
from google.genai import errors as genai_errors
from pydantic import ValidationError

from app.config import settings
from app.schemas.image import ImageTags

from functools import lru_cache

PROMPT = """You are an image classifier for a content-matching system.
Describe the main subject of this image.

Rules:
- subject: the most specific common English name of the main subject
  (e.g. "red fox", "gray wolf", "african wild dog"). Never use Latin names.
- aliases: 0 to 4 other names that are TRUE of this exact subject: broader everyday
  names (e.g. "dog" for a golden retriever puppy, "bird" for a dunlin, "bear" for a
  black bear cub, "car" for a Rover SD1) and common short forms (e.g. "hippo" for
  hippopotamus). Never list a different species or a different kind of thing.
- category: exactly one of: animal, vehicle, building, furniture, electronics, other.
- visibility: exactly one of:
    whole_subject        - the entire subject is visible and recognisable
    partial_subject      - only part of the subject is visible (a face, a wing, a wheel)
    texture_or_abstract  - a close-up of a surface, pattern or texture, or an image too
                           abstract to show a recognisable subject
- attributes: 3 to 6 short visual descriptors (colour, setting, state).
- caption: one factual sentence describing the image.
- confidence: your honest certainty from 0.0 to 1.0 that this image clearly shows the
  subject you named. If the subject is blurry, cropped too tightly, or could be
  something else, use a LOW value (below 0.5). Do not guess confidently.
"""


def is_retryable_status(code: int) -> bool:
    """Timeouts, rate limits and server-side errors are temporary; the rest are not."""
    return code in (408, 429) or code >= 500


class VisionError(Exception):
    """The API call failed (network, quota, auth)."""

    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class InvalidModelOutput(Exception):
    """The model answered, but not in the required shape. Never trusted."""

    def __init__(self, message: str, input_tokens: int = 0, output_tokens: int = 0):
        super().__init__(message)
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


@dataclass(frozen=True)
class VisionResult:
    tags: ImageTags
    model: str
    input_tokens: int
    output_tokens: int

    @property
    def validation_status(self) -> str:
        unclear = self.tags.visibility == "texture_or_abstract"
        low_confidence = self.tags.confidence < settings.min_confidence
        return "low_confidence" if (unclear or low_confidence) else "valid"


@lru_cache(maxsize=1)
def _client() -> genai.Client:
    if not settings.gemini_api_key:
        raise VisionError("GEMINI_API_KEY is not set in .env", retryable=False)
    return genai.Client(api_key=settings.gemini_api_key)


def analyze_image(path: Path) -> VisionResult:
    mime_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    image_part = types.Part.from_bytes(data=path.read_bytes(), mime_type=mime_type)
    client = _client()

    try:
        response = client.models.generate_content(
            model=settings.vision_model,
            contents=[image_part, PROMPT],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ImageTags,
                temperature=0,
            ),
        )
    except genai_errors.APIError as exc:
        code = exc.code if isinstance(exc.code, int) else 0
        raise VisionError(
            f"Gemini call failed: {exc}", retryable=is_retryable_status(code)
        ) from exc
    except Exception as exc:  # network drop, timeout ...
        raise VisionError(f"Gemini call failed: {exc}", retryable=True) from exc

    usage = response.usage_metadata
    input_tokens = (usage.prompt_token_count or 0) if usage else 0
    # Gemini 3 models can "think" before answering; thinking tokens are billed as output.
    output_tokens = (
        ((usage.candidates_token_count or 0) + (getattr(usage, "thoughts_token_count", 0) or 0))
        if usage else 0
    )

    try:
        tags = ImageTags.model_validate_json(response.text or "")
    except ValidationError as exc:
        raise InvalidModelOutput(
            f"Response failed schema validation: {exc}", input_tokens, output_tokens
        ) from exc

    return VisionResult(
        tags=tags,
        model=settings.vision_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )