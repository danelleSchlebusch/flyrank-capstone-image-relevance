from decimal import Decimal

from sqlalchemy.orm import Session

from app.config import settings
from app.models import AiUsage

from datetime import datetime, timezone

from sqlalchemy import func, select


def estimate_vision_cost(input_tokens: int, output_tokens: int) -> Decimal:
    per_million = Decimal(1_000_000)
    return (
        Decimal(input_tokens) * Decimal(str(settings.vision_input_price_per_m))
        + Decimal(output_tokens) * Decimal(str(settings.vision_output_price_per_m))
    ) / per_million


def record_vision_usage(
    db: Session, model: str, image_id: int, input_tokens: int, output_tokens: int
) -> None:
    """Adds a usage row to the session. The caller commits."""
    db.add(AiUsage(
        operation="vision",
        model=model,
        item_id=image_id,
        tokens_or_units=input_tokens + output_tokens,
        cost=estimate_vision_cost(input_tokens, output_tokens),
    ))

def spent_today(db: Session) -> Decimal:
    """Total AI cost since midnight UTC."""
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    total = db.scalar(
        select(func.coalesce(func.sum(AiUsage.cost), 0)).where(AiUsage.created_at >= start)
    )
    return Decimal(str(total))

def record_embedding_usage(db: Session, model: str, item_id: int, text: str) -> None:
    """The embedding API doesn't report token counts here, so this is an estimate
    (about 4 characters per token). It's labelled as such in the README."""
    tokens = max(1, len(text) // 4)
    cost = Decimal(tokens) * Decimal(str(settings.embedding_price_per_m)) / Decimal(1_000_000)
    db.add(AiUsage(
        operation="embedding",
        model=model,
        item_id=item_id,
        tokens_or_units=tokens,
        cost=cost,
    ))

def record_text_usage(
    db: Session, model: str, item_id: int, input_tokens: int, output_tokens: int
) -> None:
    # Same model family and prices as vision; split the settings if that ever changes.
    db.add(AiUsage(
        operation="text",
        model=model,
        item_id=item_id,
        tokens_or_units=input_tokens + output_tokens,
        cost=estimate_vision_cost(input_tokens, output_tokens),
    ))