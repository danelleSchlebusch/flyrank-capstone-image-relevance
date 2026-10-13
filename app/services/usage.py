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