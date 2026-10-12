import pytest
from pydantic import ValidationError

from app.schemas.image import ImageTags

VALID = {
    "subject": " Red Fox ",
    "category": "animal",
    "attributes": ["Orange fur", "wild"],
    "caption": "A red fox standing in a forest",
    "confidence": 0.94,
}


def test_valid_payload_is_accepted_and_normalised():
    tags = ImageTags.model_validate(VALID)
    assert tags.subject == "red fox"
    assert tags.attributes == ["orange fur", "wild"]


@pytest.mark.parametrize("field,value", [
    ("category", "wildlife"),       # not in the fixed list
    ("confidence", 94),             # out of range (percent instead of 0-1)
    ("confidence", -0.1),
    ("attributes", []),             # empty
    ("subject", ""),
    ("caption", "hi"),              # too short
])
def test_invalid_payloads_are_rejected(field, value):
    with pytest.raises(ValidationError):
        ImageTags.model_validate({**VALID, field: value})


def test_missing_field_is_rejected():
    bad = {k: v for k, v in VALID.items() if k != "caption"}
    with pytest.raises(ValidationError):
        ImageTags.model_validate(bad)