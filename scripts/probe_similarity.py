import sys

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Image, ImageMetadata, ImageVector
from app.services.embeddings import QUERY, embed_text

queries = sys.argv[1:] or [
    "a red fox in the forest",
    "a gray wolf",
    "a hippo in the water",
    "a penguin on the ice",
    "a sports car",
]

with SessionLocal() as db:
    for q in queries:
        qvec = embed_text(q, QUERY)
        distance = ImageVector.embedding.cosine_distance(qvec)
        rows = db.execute(
            select(Image.filename, ImageMetadata.subject, distance.label("distance"))
            .join(ImageVector, ImageVector.image_id == Image.id)
            .join(ImageMetadata, ImageMetadata.image_id == Image.id)
            .order_by(distance)
            .limit(4)
        ).all()
        print(f"\n'{q}'")
        for filename, subject, d in rows:
            print(f"  {1 - d:.3f}  {filename:20} {subject}")