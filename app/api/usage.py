from typing import Annotated, Literal

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import DbDep
from app.config import settings
from app.models import AiUsage
from app.schemas.api import UsageByOperation, UsageEntryOut, UsageSummaryOut
from app.services.usage import spent_today

router = APIRouter(prefix="/usage", tags=["cost"])


@router.get("/summary", response_model=UsageSummaryOut)
def summary(db: DbDep):
    rows = db.execute(
        select(
            AiUsage.operation,
            func.count(),
            func.coalesce(func.sum(AiUsage.tokens_or_units), 0),
            func.coalesce(func.sum(AiUsage.cost), 0),
        ).group_by(AiUsage.operation).order_by(AiUsage.operation)
    ).all()
    by_operation = [
        UsageByOperation(operation=op, calls=calls, tokens=int(tokens), cost=float(cost))
        for op, calls, tokens, cost in rows
    ]
    return UsageSummaryOut(
        by_operation=by_operation,
        total_calls=sum(r.calls for r in by_operation),
        total_cost=sum(r.cost for r in by_operation),
        spent_today=float(spent_today(db)),
        daily_budget_usd=settings.daily_budget_usd,
    )


@router.get("", response_model=list[UsageEntryOut])
def entries(
    db: DbDep,
    operation: Literal["vision", "embedding", "text"] | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """Every AI call, newest first, each attributed to a model, an item and a cost."""
    stmt = select(AiUsage).order_by(AiUsage.id.desc())
    if operation:
        stmt = stmt.where(AiUsage.operation == operation)
    return db.scalars(stmt.limit(limit).offset(offset)).all()