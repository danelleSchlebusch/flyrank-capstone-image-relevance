from typing import Literal

from pydantic import BaseModel, Field, field_validator

Category = Literal["animal", "vehicle", "building", "furniture", "electronics", "other"]

Visibility = Literal["whole_subject", "partial_subject", "texture_or_abstract"]

class ImageTags(BaseModel):
    """The contract for what the vision model must return."""

    subject: str = Field(min_length=1, max_length=100)
    category: Category
    aliases: list[str] = Field(default_factory=list, max_length=6)
    visibility: Visibility
    attributes: list[str] = Field(min_length=1, max_length=10)
    caption: str = Field(min_length=5, max_length=300)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("subject")
    @classmethod
    def normalise_subject(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("attributes")
    @classmethod
    def normalise_attributes(cls, v: list[str]) -> list[str]:
        cleaned = [a.strip().lower() for a in v if a.strip()]
        if not cleaned:
            raise ValueError("attributes must contain at least one non-empty item")
        return cleaned

    @field_validator("aliases")
    @classmethod
    def normalise_aliases(cls, v: list[str]) -> list[str]:
        cleaned: list[str] = []
        for alias in v:
            alias = alias.strip().lower()
            if alias and alias not in cleaned:
                cleaned.append(alias)
        return cleaned