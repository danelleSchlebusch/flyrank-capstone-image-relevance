from sqlalchemy.orm import Session

from app.config import settings
from app.models import Post, PostVector
from app.services.embeddings import QUERY, embed_text
from app.services.post_analysis import analyze_post
from app.services.usage import ensure_within_budget, record_embedding_usage, record_text_usage

MAX_POST_CHARS = 2000   # same cap as the design document


def create_post(db: Session, title: str, content: str) -> Post:
    """Read the post, embed it, and save everything in ONE transaction.
    If either AI call fails, nothing is saved."""
    ensure_within_budget(db)
    excerpt = content[:MAX_POST_CHARS]
    analysis = analyze_post(title, excerpt)
    text = f"{title}\n\n{excerpt}"
    vector = embed_text(text, QUERY)

    post = Post(
        title=title,
        content=content,
        expected_subject=analysis.tags.expected_subject,
        expected_category=analysis.tags.expected_category,
    )
    db.add(post)
    db.flush()  # assigns post.id without committing

    db.add(PostVector(post_id=post.id, embedding=vector, model=settings.embedding_model))
    record_text_usage(db, analysis.model, post.id,
                      analysis.input_tokens, analysis.output_tokens)
    record_embedding_usage(db, settings.embedding_model, post.id, text)
    db.commit()
    return post