"""`FakeEnv`: an `Env` over a dict given by the test; the machine's environment is not seen."""

from __future__ import annotations

from collections.abc import Mapping

from . import fake


@fake
class FakeEnv:
    """The variables are `values`; `set` and `unset` change them as a test needs."""

    def __init__(self, values: Mapping[str, str] | None = None) -> None:
        self.values = dict(values or {})

    def get(self, name: str, default: str | None = None) -> str | None:
        return self.values.get(name, default)

    def snapshot(self) -> dict[str, str]:
        return dict(self.values)

    def take(self, name: str) -> str:
        return self.values.pop(name, "")

    def set(self, name: str, value: str) -> None:
        self.values[name] = value

    def unset(self, name: str) -> None:
        self.values.pop(name, None)
