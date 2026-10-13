from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Image, ImageMetadata
from app.services.usage import record_vision_usage
from app.services.vision import InvalidModelOutput, VisionError, analyze_image


def tag_image(db: Session, image: Image) -> Image:
    """Analyze one image and persist the result. Idempotent: tagged images are skipped."""
    if image.meta is not None:
        return image

    image.status = "processing"
    db.commit()

    path = Path(settings.images_dir) / image.filename
    try:
        result = analyze_image(path)
    except VisionError:
        # Temporary problem (network, quota). Put it back in the queue.
        image.status = "pending"
        db.commit()
        raise
    except InvalidModelOutput as exc:
        # The call cost money even though the answer was unusable.
        record_vision_usage(db, settings.vision_model, image.id,
                            exc.input_tokens, exc.output_tokens)
        image.status = "failed"
        db.commit()
        raise

    tags = result.tags
    image.meta = ImageMetadata(
        subject=tags.subject,
        category=tags.category,
        visibility=tags.visibility,
        attributes=tags.attributes,
        caption=tags.caption,
        confidence=tags.confidence,
        validation_status=result.validation_status,
    )
    image.status = "flagged" if result.validation_status == "low_confidence" else "tagged"
    record_vision_usage(db, result.model, image.id,
                        result.input_tokens, result.output_tokens)
    db.commit()
    return image