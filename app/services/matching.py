from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ImageMetadata, ImageVector, Post, PostVector, Suggestion
from app.services.mismatch_guard import (
    Candidate, GuardSelection, GuardThresholds, ImageProfile, PostProfile, select_best,
)


def find_candidates(db: Session, post_id: int, limit: int) -> list[Candidate]:
    """The nearest images to the post, best first. pgvector does the ranking."""
    post_vector = db.scalar(select(PostVector.embedding).where(
        PostVector.post_id == post_id,
        PostVector.model == settings.embedding_model,
    ))
    if post_vector is None:
        raise ValueError(f"post {post_id} has no embedding")

    distance = ImageVector.embedding.cosine_distance(post_vector)
    rows = db.execute(
        select(ImageVector.image_id, ImageMetadata, distance.label("distance"))
        .select_from(ImageVector)
        .join(ImageMetadata, ImageMetadata.image_id == ImageVector.image_id)
        .where(ImageVector.model == settings.embedding_model)
        .order_by(distance)
        .limit(limit)
    ).all()

    return [
        Candidate(
            image_id=image_id,
            image=ImageProfile(
                subject=meta.subject,
                category=meta.category,
                confidence=meta.confidence,
                validation_status=meta.validation_status,
                aliases=tuple(meta.aliases or []),
            ),
            similarity=float(1 - dist),
        )
        for image_id, meta, dist in rows
    ]


def match_post(
    db: Session,
    post_id: int,
    thresholds: GuardThresholds | None = None,
    limit: int | None = None,
) -> GuardSelection:
    post = db.get(Post, post_id)
    thresholds = thresholds or GuardThresholds(settings.min_similarity, settings.min_confidence)
    candidates = find_candidates(db, post_id, limit or settings.match_top_k)
    selection = select_best(
        PostProfile(post.expected_subject, post.expected_category), candidates, thresholds
    )
    _save_suggestions(db, post_id, selection)
    return selection


def _save_suggestions(db: Session, post_id: int, selection: GuardSelection) -> None:
    """Record every evaluated candidate, with the reason. Updating rows in place
    (not deleting them) keeps any human reviews attached."""
    existing = {s.image_id: s for s in db.scalars(
        select(Suggestion).where(Suggestion.post_id == post_id))}
    chosen_id = selection.chosen.image_id if selection.chosen else None
    evaluated: set[int] = set()

    for candidate, result in selection.evaluations:
        evaluated.add(candidate.image_id)
        row = existing.get(candidate.image_id)
        if row is None:
            row = Suggestion(post_id=post_id, image_id=candidate.image_id)
            db.add(row)
        row.similarity_score = candidate.similarity
        row.decision = "suggested" if candidate.image_id == chosen_id else "rejected"
        row.reason = result.explanation

    # A suggestion from an earlier run that this run didn't evaluate is out of date.
    for image_id, row in existing.items():
        if image_id not in evaluated and row.decision == "suggested":
            row.decision = "rejected"
            row.reason = "Superseded by a later matching run"

    db.commit()