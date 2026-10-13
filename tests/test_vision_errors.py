import pytest

from app.services.vision import is_retryable_status


@pytest.mark.parametrize("code", [408, 429, 500, 503])
def test_temporary_errors_are_retryable(code):
    assert is_retryable_status(code)


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_permanent_errors_are_not_retryable(code):
    assert not is_retryable_status(code)