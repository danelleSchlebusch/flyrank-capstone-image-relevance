import pytest

from app.config import settings
from app.services.retry import call_with_backoff


class Temporary(Exception):
    retryable = True


class Permanent(Exception):
    retryable = False


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("app.services.retry.time.sleep", lambda seconds: None)


def test_retries_temporary_errors_then_succeeds():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise Temporary("503")
        return "ok"

    assert call_with_backoff(flaky) == "ok"
    assert calls["n"] == 3


def test_permanent_errors_are_not_retried():
    calls = {"n": 0}

    def broken():
        calls["n"] += 1
        raise Permanent("bad key")

    with pytest.raises(Permanent):
        call_with_backoff(broken)
    assert calls["n"] == 1


def test_gives_up_after_max_attempts():
    calls = {"n": 0}

    def always_down():
        calls["n"] += 1
        raise Temporary("503")

    with pytest.raises(Temporary):
        call_with_backoff(always_down)
    assert calls["n"] == settings.job_max_attempts