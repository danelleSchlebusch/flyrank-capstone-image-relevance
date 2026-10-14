import logging
import random
import time
from collections.abc import Callable
from typing import TypeVar

from app.config import settings

log = logging.getLogger(__name__)
T = TypeVar("T")


def call_with_backoff(func: Callable[[], T], label: str = "call") -> T:
    """Run func, retrying temporary errors (exceptions with .retryable = True)
    with exponential backoff and jitter."""
    for attempt in range(1, settings.job_max_attempts + 1):
        try:
            return func()
        except Exception as exc:
            retryable = getattr(exc, "retryable", False)
            if not retryable or attempt == settings.job_max_attempts:
                raise
            delay = settings.job_backoff_base_seconds * 2 ** (attempt - 1) + random.uniform(0, 1)
            log.warning("%s: attempt %d failed, retrying in %.1fs", label, attempt, delay)
            time.sleep(delay)
    raise AssertionError("unreachable")