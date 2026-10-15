from typing import Annotated

from fastapi import APIRouter, Path, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import DbDep
from app.models import Image
from app.schemas.api import ImageOut, ImageStatus
from app.services.errors import NotFoundError

router = APIRouter(prefix="/images", tags=["images"])


@router.get("", response_model=list[ImageOut])
def list_images(
    db: DbDep,
    status: ImageStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    stmt = select(Image).options(selectinload(Image.meta)).order_by(Image.id)
    if status:
        stmt = stmt.where(Image.status == status)
    return db.scalars(stmt.limit(limit).offset(offset)).all()


@router.get("/{image_id}", response_model=ImageOut)
def get_image(image_id: Annotated[int, Path(ge=1)], db: DbDep):
    image = db.get(Image, image_id)
    if image is None:
        raise NotFoundError(f"image {image_id} not found")
    return image