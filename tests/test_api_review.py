import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
FOX_TITLE = "How red foxes survive the winter"


def _fox_suggestions():
    posts = client.get("/posts", params={"limit": 200}).json()
    post_id = next((p["id"] for p in posts if p["title"] == FOX_TITLE), None)
    if post_id is None:
        pytest.skip("run scripts.evaluate first to create the demo posts")
    client.get(f"/posts/{post_id}/images")            # makes sure suggestions are stored
    return client.get("/suggestions", params={"post_id": post_id}).json()


def test_review_input_is_validated():
    assert client.post("/suggestions/1/review", json={"decision": "maybe"}).status_code == 422
    assert client.post("/suggestions/1/review", json={}).status_code == 422
    assert client.get("/suggestions/0").status_code == 422
    assert client.get("/suggestions?decision=bogus").status_code == 422


def test_unknown_suggestion_is_404():
    assert client.get("/suggestions/999999").status_code == 404
    assert client.post("/suggestions/999999/review",
                       json={"decision": "approved"}).status_code == 404


def test_guard_rejected_pairing_cannot_be_approved():
    rejected = next(s for s in _fox_suggestions() if s["decision"] == "rejected")
    response = client.post(f"/suggestions/{rejected['id']}/review",
                           json={"decision": "approved"})
    assert response.status_code == 409


def test_approving_the_suggestion_is_idempotent():
    chosen = next(s for s in _fox_suggestions() if s["decision"] == "suggested")
    body = {"decision": "approved", "comment": "Correct fox"}
    first = client.post(f"/suggestions/{chosen['id']}/review", json=body)
    second = client.post(f"/suggestions/{chosen['id']}/review", json=body)
    assert first.status_code in (200, 201)
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    inspected = client.get(f"/suggestions/{chosen['id']}").json()
    assert inspected["review_status"] == "approved"
    assert inspected["reason"]


def test_cost_log_attributes_every_call():
    summary = client.get("/usage/summary").json()
    operations = {row["operation"] for row in summary["by_operation"]}
    assert {"vision", "embedding"} <= operations
    entries = client.get("/usage", params={"operation": "vision", "limit": 5}).json()
    assert entries and all({"model", "item_id", "cost"} <= set(e) for e in entries)
    assert client.get("/usage?operation=bogus").status_code == 422


def test_job_endpoints_validate_input():
    assert client.get("/jobs").status_code == 200
    assert client.get("/jobs/999999").status_code == 404
    assert client.post("/jobs/process-images?retry_failed=maybe").status_code == 422