from typing import Annotated

from fastapi import APIRouter, Path, Query
from sqlalchemy import select

from app.api.deps import DbDep
from app.config import settings
from app.models import Post
from app.schemas.api import (
    CheckIn, CheckOut, MatchOut, PostIn, PostOut, RankedImageOut, ReasonOut, ThresholdsOut,
)
from app.services.errors import NotFoundError
from app.services.matching import check_candidate, match_post
from app.services.mismatch_guard import Candidate, GuardResult
from app.services.posts import create_post

router = APIRouter(prefix="/posts", tags=["posts"])

PostId = Annotated[int, Path(ge=1)]


def _reasons(result: GuardResult) -> list[ReasonOut]:
    return [ReasonOut(code=r.code.value, message=r.message) for r in result.reasons]


def _thresholds() -> ThresholdsOut:
    return ThresholdsOut(min_similarity=settings.min_similarity,
                         min_confidence=settings.min_confidence)


def _ranked(rank: int, cand: Candidate, result: GuardResult, chosen_id: int | None) -> RankedImageOut:
    return RankedImageOut(
        rank=rank,
        image_id=cand.image_id,
        filename=cand.filename,
        subject=cand.image.subject,
        category=cand.image.category,
        similarity=round(cand.similarity, 4),
        guard_passed=not result.reasons,
        is_suggestion=cand.image_id == chosen_id,
        reasons=_reasons(result),
    )


@router.post("", response_model=PostOut, status_code=201)
def create(payload: PostIn, db: DbDep):
    # Two quick AI calls (read the post, embed it). The slow, bulk work is the
    # image tagging job; it never runs inside a request.
    return create_post(db, payload.title, payload.content)


@router.get("", response_model=list[PostOut])
def list_posts(
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return db.scalars(select(Post).order_by(Post.id).limit(limit).offset(offset)).all()


@router.get("/{post_id}", response_model=PostOut)
def get_post(post_id: PostId, db: DbDep):
    post = db.get(Post, post_id)
    if post is None:
        raise NotFoundError(f"post {post_id} not found")
    return post


@router.get("/{post_id}/images", response_model=MatchOut)
def ranked_images(
    post_id: PostId,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=100)] = settings.match_top_k,
):
    """Rank the images for a post, run the guard on each, and suggest the best
    one that passes, or answer 'no confident match'. Idempotent: calling it
    again updates the same stored suggestions instead of adding new ones."""
    selection = match_post(db, post_id, limit=limit)
    chosen_id = selection.chosen.image_id if selection.chosen else None
    candidates = [
        _ranked(rank, cand, result, chosen_id)
        for rank, (cand, result) in enumerate(selection.evaluations, start=1)
    ]
    return MatchOut(
        post_id=post_id,
        decision=selection.decision.value,
        explanation=selection.explanation,
        suggested_image=next((c for c in candidates if c.is_suggestion), None),
        thresholds=_thresholds(),
        candidates=candidates,
    )


@router.post("/{post_id}/check", response_model=CheckOut)
def check(post_id: PostId, payload: CheckIn, db: DbDep):
    """Force one specific image as a candidate and see the guard's verdict."""
    cand, result = check_candidate(
        db, post_id, image_id=payload.image_id, filename=payload.filename
    )
    return CheckOut(
        post_id=post_id,
        image_id=cand.image_id,
        filename=cand.filename,
        subject=cand.image.subject,
        similarity=round(cand.similarity, 4),
        decision="rejected" if result.reasons else "accepted",
        explanation=result.explanation,
        reasons=_reasons(result),
        thresholds=_thresholds(),
    )