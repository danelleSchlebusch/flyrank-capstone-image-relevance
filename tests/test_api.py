import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _post_id(title: str):
    for post in client.get("/posts", params={"limit": 200}).json():
        if post["title"] == title:
            return post["id"]
    return None


def test_health():
    assert client.get("/health").json()["database"] == "connected"


@pytest.mark.parametrize("path", ["/images/0", "/images/abc", "/images?status=bogus",
                                  "/images?limit=0", "/posts/0", "/posts?offset=-1"])
def test_bad_input_is_422_not_500(path):
    assert client.get(path).status_code == 422


def test_unknown_records_are_404():
    assert client.get("/images/999999").status_code == 404
    assert client.get("/posts/999999").status_code == 404
    assert client.get("/posts/999999/images").status_code == 404


def test_post_body_is_validated_before_any_ai_call():
    assert client.post("/posts", json={"title": "x", "content": "short"}).status_code == 422
    assert client.post("/posts", json={}).status_code == 422


def test_check_needs_exactly_one_image_identifier():
    assert client.post("/posts/1/check", json={}).status_code == 422
    both = {"image_id": 1, "filename": "wolf.jpg"}
    assert client.post("/posts/1/check", json=both).status_code == 422


def test_wolf_is_rejected_for_the_fox_post():
    post_id = _post_id("How red foxes survive the winter")
    if post_id is None:
        pytest.skip("run scripts.evaluate first to create the demo posts")
    body = client.post(f"/posts/{post_id}/check", json={"filename": "wolf.jpg"}).json()
    assert body["decision"] == "rejected"
    assert any("mismatch" in r["message"].lower() for r in body["reasons"])


def test_fox_post_ranks_the_fox_first():
    post_id = _post_id("How red foxes survive the winter")
    if post_id is None:
        pytest.skip("run scripts.evaluate first to create the demo posts")
    body = client.get(f"/posts/{post_id}/images").json()
    assert body["decision"] == "suggested"
    assert body["candidates"][0]["filename"] == "fox.jpg"
    assert body["suggested_image"]["filename"] == "fox.jpg"