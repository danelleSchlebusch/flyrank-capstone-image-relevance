"""Prepare demo data.

python -m scripts.seed                  load the committed snapshot (no API key needed)
python -m scripts.seed --images-only    only register the image files as 'pending',
                                        so you can run the batch job live
"""
import argparse
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select

from app.config import settings
from app.database import SessionLocal
from app.models import (
    AiUsage, Image, ImageMetadata, ImageVector, Post, PostVector,
)
from app.services.ingestion import ingest_directory

SNAPSHOT = Path("data/seed/snapshot.json")


def load_snapshot(db) -> None:
    data = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    if data["embedding_dim"] != settings.embedding_dim:
        raise SystemExit("snapshot embedding size does not match this schema")
    model = data["embedding_model"]

    print("images registered:", ingest_directory(db))
    images = {i.filename: i for i in db.scalars(select(Image))}

    tagged = 0
    for entry in data["images"]:
        image = images.get(entry["filename"])
        if image is None:
            print(f"  skipped {entry['filename']}: file not found in {settings.images_dir}")
            continue
        if image.meta is None:
            image.meta = ImageMetadata(**entry["metadata"])
            image.status = entry["status"]
            tagged += 1
        has_vector = db.scalar(select(ImageVector.id).where(
            ImageVector.image_id == image.id, ImageVector.model == model))
        if entry["embedding"] and not has_vector:
            db.add(ImageVector(image_id=image.id, embedding=entry["embedding"], model=model))
    db.commit()

    created_posts = 0
    for entry in data["posts"]:
        post = db.scalar(select(Post).where(Post.title == entry["title"]))
        if post is None:
            post = Post(title=entry["title"], content=entry["content"],
                        expected_subject=entry["expected_subject"],
                        expected_category=entry["expected_category"])
            db.add(post)
            db.flush()
            created_posts += 1
        has_vector = db.scalar(select(PostVector.id).where(
            PostVector.post_id == post.id, PostVector.model == model))
        if entry["embedding"] and not has_vector:
            db.add(PostVector(post_id=post.id, embedding=entry["embedding"], model=model))
    db.commit()

    loaded_usage = 0
    if db.scalar(select(func.count()).select_from(AiUsage)) == 0:
        image_ids = {name: i.id for name, i in images.items()}
        post_ids = {p.title: p.id for p in db.scalars(select(Post))}
        for u in data["usage"]:
            ref = u["item"] or {}
            if "image" in ref:
                item_id = image_ids.get(ref["image"])
            elif "post" in ref:
                item_id = post_ids.get(ref["post"])
            else:
                item_id = None
            db.add(AiUsage(
                operation=u["operation"], model=u["model"], item_id=item_id,
                tokens_or_units=u["tokens_or_units"], cost=Decimal(u["cost"]),
                created_at=datetime.fromisoformat(u["created_at"]),
            ))
            loaded_usage += 1
        db.commit()

    print(f"tags loaded: {tagged} | posts created: {created_posts} | "
          f"usage rows loaded: {loaded_usage}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--images-only", action="store_true")
    args = parser.parse_args()

    with SessionLocal() as db:
        if args.images_only:
            print("images registered:", ingest_directory(db))
        else:
            load_snapshot(db)


if __name__ == "__main__":
    main()