"""Field checkers shared by the TOML-defined records (schedules, triggers).

A checker is `(value, errors) -> typed value`: it appends what is wrong to `errors` and
returns None (or an empty value) for a bad one. The messages are user-visible and pinned
by tests/test_tomldir.py.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Check = Callable[[Any, list[str]], Any]


def is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def str_list(name: str, v: Any, errors: list[str]) -> list[str]:
    if isinstance(v, str):
        v = [x.strip() for x in v.split(",") if x.strip()]
    if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
        errors.append(f"{name} must be a list of non-empty strings, got {v!r}")
        return []
    return [x.strip() for x in v]


def text(name: str, what: str) -> Check:
    def check(v: Any, errors: list[str]) -> Any:
        if not isinstance(v, str):
            errors.append(f"{name} must be {what}, got {v!r}")
        return v.strip() if isinstance(v, str) else None

    return check


def one_of(name: str, allowed: tuple[str, ...]) -> Check:
    def check(v: Any, errors: list[str]) -> Any:
        if v not in allowed:
            errors.append(f"{name} must be one of {', '.join(allowed)}, got {v!r}")
            return None
        return v

    return check


def flag(name: str) -> Check:
    def check(v: Any, errors: list[str]) -> Any:
        if not isinstance(v, bool):
            errors.append(f"{name} must be true or false, got {v!r}")
            return None
        return v

    return check


def whole(name: str, lo: int, hi: int | None = None) -> Check:
    def check(v: Any, errors: list[str]) -> Any:
        if not isinstance(v, int) or isinstance(v, bool) or v < lo or (hi and v > hi):
            top = f" and at most {hi}" if hi else " or more"
            errors.append(f"{name} must be a whole number, {lo}{top}, got {v!r}")
            return None
        return v

    return check


def str_map(name: str) -> Check:
    def check(v: Any, errors: list[str]) -> Any:
        if not isinstance(v, dict) or not all(
            isinstance(k, str) and isinstance(x, str) for k, x in v.items()
        ):
            errors.append(f"{name} must be a table of strings, got {v!r}")
            return None
        return dict(v)

    return check
