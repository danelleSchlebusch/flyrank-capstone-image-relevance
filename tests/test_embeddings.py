from app.services.embeddings import build_image_text


def test_image_text_contains_every_field():
    text = build_image_text("red fox", "animal", "A fox in a forest.", ["orange fur", "wild"])
    assert "red fox" in text
    assert "animal" in text
    assert "A fox in a forest." in text
    assert "orange fur, wild" in text