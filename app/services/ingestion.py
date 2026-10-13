from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Image


def ingest_directory(db: Session, directory: str | None = None) -> dict[str, int]:
    """Register every image file as a 'pending' row. Safe to run repeatedly."""
    folder = Path(directory or settings.images_dir)
    existing = set(db.scalars(select(Image.filename)))

    created = skipped = 0
    for path in sorted(folder.glob("*.jpg")):
        if path.name in existing:
            skipped += 1
            continue
        db.add(Image(
            filename=path.name,
            image_url=f"/images/{path.name}",
            status="pending",
        ))
        created += 1

    db.commit()
    return {"created": created, "skipped": skipped}