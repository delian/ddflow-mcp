"""The `Env` port and its default adapter: the ONE place ddflow reads process environment.

A caller that takes an `Env` (or a `Mapping` from `snapshot()`) can be tested with a given
environment instead of the machine's. `get` reads the live environment each call, as
`os.environ.get` did; `snapshot` is a copy, safe to mutate and to hand to a child process.
Stdlib only, no other ddflow layer.
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

from .ports import port


@port
@runtime_checkable
class Env(Protocol):
    """Process environment, read-only. Home: `ddflow.adapters.env` (`SystemEnv`); fake `FakeEnv`."""

    def get(self, name: str, default: str = "") -> str:
        """The variable's value, or `default` when it is not set."""

    def snapshot(self) -> dict[str, str]:
        """A copy of every variable now."""


class SystemEnv:
    """The default adapter: `os.environ`, read live. Stateless."""

    def get(self, name: str, default: str = "") -> str:
        return os.environ.get(name, default)

    def snapshot(self) -> dict[str, str]:
        return dict(os.environ)


#: The one stateless instance callers default to; a test passes `FakeEnv` instead.
DEFAULT = SystemEnv()


def default_env() -> SystemEnv:
    """The environment a caller uses when it was handed none."""
    return DEFAULT
