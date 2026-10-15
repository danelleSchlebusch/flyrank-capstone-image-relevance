from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Review, Suggestion
from app.services.errors import ConflictError, NotFoundError


def add_review(
    db: Session, suggestion_id: int, decision: str, comment: str | None
) -> tuple[Review, bool]:
    """Record a human decision. Returns (review, created).

    Idempotent: repeating the latest review exactly returns it unchanged.
    """
    suggestion = db.get(Suggestion, suggestion_id)
    if suggestion is None:
        raise NotFoundError(f"suggestion {suggestion_id} not found")

    if decision == "approved" and suggestion.decision == "rejected":
        raise ConflictError(
            "The mismatch guard rejected this pairing, so it cannot be approved: "
            + suggestion.reason
        )

    latest = db.scalar(
        select(Review).where(Review.suggestion_id == suggestion_id)
        .order_by(Review.id.desc()).limit(1)
    )
    if latest and latest.decision == decision and latest.comment == comment:
        return latest, False

    review = Review(suggestion_id=suggestion_id, decision=decision, comment=comment)
    db.add(review)
    db.commit()
    db.refresh(review)
    return review, True