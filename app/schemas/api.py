from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ImageStatus = Literal["pending", "processing", "tagged", "flagged", "failed"]


class ImageMetadataOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    subject: str
    category: str
    visibility: str
    aliases: list[str]
    attributes: list[str]
    caption: str
    confidence: float
    validation_status: str


class ImageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    filename: str
    image_url: str
    status: ImageStatus
    metadata: ImageMetadataOut | None = Field(default=None, validation_alias="meta")


class PostIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    title: str = Field(min_length=3, max_length=300)
    content: str = Field(min_length=20, max_length=20_000)


class PostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    content: str
    expected_subject: str | None
    expected_category: str | None
    created_at: datetime


class ReasonOut(BaseModel):
    code: str
    message: str


class ThresholdsOut(BaseModel):
    min_similarity: float
    min_confidence: float


class RankedImageOut(BaseModel):
    rank: int
    image_id: int
    filename: str
    subject: str
    category: str
    similarity: float
    guard_passed: bool
    is_suggestion: bool
    reasons: list[ReasonOut]


class MatchOut(BaseModel):
    post_id: int
    decision: Literal["suggested", "no_confident_match"]
    explanation: str
    suggested_image: RankedImageOut | None
    thresholds: ThresholdsOut
    candidates: list[RankedImageOut]


class CheckIn(BaseModel):
    image_id: int | None = Field(default=None, ge=1)
    filename: str | None = Field(default=None, min_length=1, max_length=255)

    @model_validator(mode="after")
    def exactly_one_identifier(self):
        if (self.image_id is None) == (self.filename is None):
            raise ValueError("provide exactly one of image_id or filename")
        return self


class CheckOut(BaseModel):
    post_id: int
    image_id: int
    filename: str
    subject: str
    similarity: float
    decision: Literal["accepted", "rejected"]
    explanation: str
    reasons: list[ReasonOut]
    thresholds: ThresholdsOut