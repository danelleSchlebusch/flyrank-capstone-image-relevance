"""Write data/seed/snapshot.json from the current database.

Run this once, on a database whose tags you are happy with. The snapshot lets
a stranger reproduce your numbers exactly, with no API key.
"""
import json
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import AiUsage, Image, ImageVector, Post, PostVector

OUT = Path("data/seed/snapshot.json")


def vec(values) -> list[float]:
    return [round(float(v), 6) for v in values]


def main() -> None:
    model = settings.embedding_model
    with SessionLocal() as db:
        image_vecs = {v.image_id: v for v in db.scalars(
            select(ImageVector).where(ImageVector.model == model))}
        post_vecs = {v.post_id: v for v in db.scalars(
            select(PostVector).where(PostVector.model == model))}

        names: dict[int, str] = {}
        images = []
        for image in db.scalars(select(Image).order_by(Image.id)):
            names[image.id] = image.filename
            if image.meta is None:
                continue
            m = image.meta
            v = image_vecs.get(image.id)
            images.append({
                "filename": image.filename,
                "status": image.status,
                "metadata": {
                    "subject": m.subject, "category": m.category,
                    "visibility": m.visibility, "aliases": m.aliases,
                    "attributes": m.attributes, "caption": m.caption,
                    "confidence": m.confidence,
                    "validation_status": m.validation_status,
                },
                "embedding": vec(v.embedding) if v else None,
            })

        titles: dict[int, str] = {}
        posts = []
        for post in db.scalars(select(Post).order_by(Post.id)):
            titles[post.id] = post.title
            v = post_vecs.get(post.id)
            posts.append({
                "title": post.title, "content": post.content,
                "expected_subject": post.expected_subject,
                "expected_category": post.expected_category,
                "embedding": vec(v.embedding) if v else None,
            })

        # The usage table stores only a numeric item id. Vision rows are about an
        # image and text rows about a post. An embedding row is about a post when
        # it directly follows that post's text row (create_post logs them back to
        # back); otherwise it is about an image.
        usage = []
        previous = None
        for row in db.scalars(select(AiUsage).order_by(AiUsage.id)):
            if row.operation == "vision":
                item = {"image": names.get(row.item_id)}
            elif row.operation == "text":
                item = {"post": titles.get(row.item_id)}
            elif (previous and previous.operation == "text"
                  and previous.item_id == row.item_id):
                item = {"post": titles.get(row.item_id)}
            else:
                item = {"image": names.get(row.item_id)}
            usage.append({
                "operation": row.operation, "model": row.model, "item": item,
                "tokens_or_units": row.tokens_or_units, "cost": str(row.cost),
                "created_at": row.created_at.isoformat(),
            })
            previous = row

    snapshot = {
        "embedding_model": model,
        "embedding_dim": settings.embedding_dim,
        "images": images, "posts": posts, "usage": usage,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(snapshot, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUT}: {len(images)} images, {len(posts)} posts, "
          f"{len(usage)} usage rows, {OUT.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()