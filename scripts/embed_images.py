import logging
import time

from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import Image, ImageMetadata
from app.services.embeddings import embed_image

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

with SessionLocal() as db:
    images = db.scalars(
        select(Image).join(ImageMetadata).order_by(Image.id)
    ).all()
    done = skipped = 0
    for image in images:
        if embed_image(db, image):
            done += 1
            time.sleep(settings.job_delay_seconds)
        else:
            skipped += 1
    print(f"embedded {done}, already done {skipped}, total {len(images)}")