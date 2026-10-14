import pytest
from pydantic import ValidationError

from app.schemas.post import PostSubject


def test_subject_is_normalised():
    p = PostSubject.model_validate({"subject": " Red Fox ", "category": "animal"})
    assert p.expected_subject == "red fox"
    assert p.expected_category == "animal"


def test_no_subject_maps_to_none():
    p = PostSubject.model_validate({"subject": "", "category": "unclear"})
    assert p.expected_subject is None
    assert p.expected_category is None


def test_invalid_category_is_rejected():
    with pytest.raises(ValidationError):
        PostSubject.model_validate({"subject": "fox", "category": "wildlife"})