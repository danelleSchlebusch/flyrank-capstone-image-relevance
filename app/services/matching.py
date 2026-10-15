from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Image, ImageMetadata, ImageVector, Post, PostVector, Suggestion
from app.services.errors import ConflictError, NotFoundError
from app.services.mismatch_guard import (
    Candidate, GuardResult, GuardSelection, GuardThresholds, ImageProfile, PostProfile,
    evaluate_match, select_best,
)


def _profile(meta: ImageMetadata) -> ImageProfile:
    return ImageProfile(
        subject=meta.subject,
        category=meta.category,
        confidence=meta.confidence,
        validation_status=meta.validation_status,
        aliases=tuple(meta.aliases or []),
    )


def _thresholds(thresholds: GuardThresholds | None) -> GuardThresholds:
    return thresholds or GuardThresholds(settings.min_similarity, settings.min_confidence)


def _post_vector(db: Session, post_id: int):
    vector = db.scalar(select(PostVector.embedding).where(
        PostVector.post_id == post_id,
        PostVector.model == settings.embedding_model,
    ))
    if vector is None:
        raise ConflictError(f"post {post_id} has no embedding yet")
    return vector


def find_candidates(db: Session, post_id: int, limit: int) -> list[Candidate]:
    """The nearest images to the post, best first. pgvector does the ranking."""
    post_vector = _post_vector(db, post_id)
    distance = ImageVector.embedding.cosine_distance(post_vector)
    rows = db.execute(
        select(ImageVector.image_id, Image.filename, ImageMetadata, distance.label("distance"))
        .select_from(ImageVector)
        .join(Image, Image.id == ImageVector.image_id)
        .join(ImageMetadata, ImageMetadata.image_id == ImageVector.image_id)
        .where(ImageVector.model == settings.embedding_model)
        .order_by(distance)
        .limit(limit)
    ).all()
    return [
        Candidate(image_id=image_id, image=_profile(meta),
                  similarity=float(1 - dist), filename=filename)
        for image_id, filename, meta, dist in rows
    ]


def match_post(
    db: Session,
    post_id: int,
    thresholds: GuardThresholds | None = None,
    limit: int | None = None,
) -> GuardSelection:
    post = db.get(Post, post_id)
    if post is None:
        raise NotFoundError(f"post {post_id} not found")
    candidates = find_candidates(db, post_id, limit or settings.match_top_k)
    selection = select_best(
        PostProfile(post.expected_subject, post.expected_category),
        candidates,
        _thresholds(thresholds),
    )
    _save_suggestions(db, post_id, selection)
    return selection


def check_candidate(
    db: Session,
    post_id: int,
    *,
    image_id: int | None = None,
    filename: str | None = None,
    thresholds: GuardThresholds | None = None,
) -> tuple[Candidate, GuardResult]:
    """Force one specific image as a candidate for a post and ask the guard
    for its verdict. Read-only: nothing is saved."""
    post = db.get(Post, post_id)
    if post is None:
        raise NotFoundError(f"post {post_id} not found")

    if image_id is not None:
        image = db.get(Image, image_id)
    else:
        image = db.scalar(select(Image).where(Image.filename == filename))
    if image is None:
        raise NotFoundError("image not found")
    if image.meta is None:
        raise ConflictError(f"{image.filename} has not been tagged yet")

    distance = db.scalar(select(
        ImageVector.embedding.cosine_distance(_post_vector(db, post_id))
    ).where(
        ImageVector.image_id == image.id,
        ImageVector.model == settings.embedding_model,
    ))
    if distance is None:
        raise ConflictError(f"{image.filename} has not been embedded yet")

    profile = _profile(image.meta)
    similarity = float(1 - distance)
    result = evaluate_match(
        PostProfile(post.expected_subject, post.expected_category),
        profile, similarity, _thresholds(thresholds),
    )
    return Candidate(image.id, profile, similarity, image.filename), result


def _save_suggestions(db: Session, post_id: int, selection: GuardSelection) -> None:
    """Record every evaluated candidate with its reason. Rows are updated in
    place (never deleted), so human reviews stay attached. Idempotent."""
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
        row.reason = result.explanation
        if candidate.image_id == chosen_id:
            row.decision = "suggested"
        elif result.reasons:
            row.decision = "rejected"
        else:
            row.decision = "alternative"   # passed the guard, ranked lower than the pick

    for image_id, row in existing.items():
        if image_id not in evaluated and row.decision in ("suggested", "alternative"):
            row.decision = "rejected"
            row.reason = "Superseded by a later matching run"

    db.commit()