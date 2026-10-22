from decimal import Decimal

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

client = TestClient(app)


def test_post_creation_is_refused_when_over_budget(monkeypatch):
    monkeypatch.setattr(settings, "daily_budget_usd", 1.0)
    monkeypatch.setattr("app.services.usage.spent_today", lambda db: Decimal("999"))
    response = client.post("/posts", json={
        "title": "A valid title",
        "content": "A perfectly valid post body that is long enough to pass validation.",
    })
    assert response.status_code == 429