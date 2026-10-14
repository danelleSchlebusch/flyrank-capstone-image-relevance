from typing import Literal

from pydantic import BaseModel, Field, field_validator

PostCategory = Literal[
    "animal", "vehicle", "building", "furniture", "electronics", "other", "unclear"
]


class PostSubject(BaseModel):
    """What a post is mainly about. This is what the guard compares images against."""

    subject: str = Field(max_length=100)   # empty string = no concrete subject
    category: PostCategory

    @field_validator("subject")
    @classmethod
    def normalise_subject(cls, v: str) -> str:
        return v.strip().lower()

    @property
    def expected_subject(self) -> str | None:
        return self.subject or None

    @property
    def expected_category(self) -> str | None:
        return None if self.category == "unclear" else self.category