import mimetypes
from dataclasses import dataclass
from pathlib import Path

from google import genai
from google.genai import types
from pydantic import ValidationError

from app.config import settings
from app.schemas.image import ImageTags

from functools import lru_cache

PROMPT = """You are an image classifier for a content-matching system.
Describe the main subject of this image.

Rules:
- subject: the most specific common English name of the main subject
  (e.g. "red fox", "gray wolf", "african wild dog"). Never use Latin names.
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


class VisionError(Exception):
    """The API call failed (network, quota, auth). Worth retrying."""


class InvalidModelOutput(Exception):
    """The model answered, but not in the required shape. Never trusted."""


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
        raise VisionError("GEMINI_API_KEY is not set in .env")
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
    except Exception as exc:  # network, quota, auth ...
        raise VisionError(f"Gemini call failed: {exc}") from exc

    try:
        tags = ImageTags.model_validate_json(response.text or "")
    except ValidationError as exc:
        raise InvalidModelOutput(f"Response failed schema validation: {exc}") from exc

    usage = response.usage_metadata
    return VisionResult(
        tags=tags,
        model=settings.vision_model,
        input_tokens=(usage.prompt_token_count or 0) if usage else 0,
        output_tokens=(usage.candidates_token_count or 0) if usage else 0,
    )