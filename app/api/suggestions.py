from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import DbDep
from app.models import Suggestion
from app.schemas.api import ReviewIn, ReviewOut, SuggestionOut
from app.services.errors import NotFoundError
from app.services.reviews import add_review

router = APIRouter(prefix="/suggestions", tags=["review"])

SuggestionId = Annotated[int, Path(ge=1)]


def to_out(s: Suggestion) -> SuggestionOut:
    reviews = sorted(s.reviews, key=lambda r: r.id)
    return SuggestionOut(
        id=s.id,
        post_id=s.post_id,
        image_id=s.image_id,
        filename=s.image.filename,
        similarity_score=round(s.similarity_score, 4),
        decision=s.decision,
        reason=s.reason,
        review_status=reviews[-1].decision if reviews else "pending",
        reviews=[ReviewOut.model_validate(r) for r in reviews],
    )


@router.get("", response_model=list[SuggestionOut])
def list_suggestions(
    db: DbDep,
    post_id: Annotated[int | None, Query(ge=1)] = None,
    decision: Literal["suggested", "alternative", "rejected"] | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    stmt = (
        select(Suggestion)
        .options(selectinload(Suggestion.image), selectinload(Suggestion.reviews))
        .order_by(Suggestion.post_id, Suggestion.similarity_score.desc())
    )
    if post_id is not None:
        stmt = stmt.where(Suggestion.post_id == post_id)
    if decision is not None:
        stmt = stmt.where(Suggestion.decision == decision)
    return [to_out(s) for s in db.scalars(stmt.limit(limit).offset(offset))]


@router.get("/{suggestion_id}", response_model=SuggestionOut)
def inspect_suggestion(suggestion_id: SuggestionId, db: DbDep):
    """Why was this image selected or refused, and what have reviewers said?"""
    suggestion = db.get(Suggestion, suggestion_id)
    if suggestion is None:
        raise NotFoundError(f"suggestion {suggestion_id} not found")
    return to_out(suggestion)


@router.post("/{suggestion_id}/review", response_model=ReviewOut, status_code=201)
def review(suggestion_id: SuggestionId, payload: ReviewIn, db: DbDep, response: Response):
    """Approve or reject. 201 for a new review, 200 if it repeats the latest one."""
    record, created = add_review(db, suggestion_id, payload.decision, payload.comment)
    if not created:
        response.status_code = 200
    return record