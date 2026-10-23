"""Whether a start text is being MEASURED rather than delivered (B-doctor-no-session-start-cost).

A leaf module so every layer can ask: the upgrade notice is said once per version and the
brief sweeps finished worktrees, and neither may happen while `doctor` only counts them."""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager

_MEASURING: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "ddflow_measuring_start_cost", default=False
)


@contextmanager
def measuring() -> Iterator[None]:
    """Inside this block a text is being MEASURED, not delivered."""
    token = _MEASURING.set(True)
    try:
        yield
    finally:
        _MEASURING.reset(token)


def is_measuring() -> bool:
    return _MEASURING.get()
