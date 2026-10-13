import sys

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Image
from app.services.ingestion import ingest_directory
from app.services.tagging import tag_image

names = sys.argv[1:] or ["fox", "wolf", "feathers", "bird", "hair", "unknown"]

with SessionLocal() as db:
    print("ingest:", ingest_directory(db))
    for name in names:
        image = db.scalar(select(Image).where(Image.filename == f"{name}.jpg"))
        if image is None:
            print(f"{name}: not found")
            continue
        tag_image(db, image)
        m = image.meta
        print(f"{name:12} {image.status:8} {m.subject:20} {m.category:10} conf={m.confidence}")