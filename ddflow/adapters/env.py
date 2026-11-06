"""The `Env` port and its default adapter: the ONE place ddflow reads process environment.

A caller that takes an `Env` (or a `Mapping` from `snapshot()`) can be tested with a given
environment instead of the machine's. `get` reads the live environment each call, as
`os.environ.get` did; `snapshot` is a copy, safe to mutate and to hand to a child process;
`take` reads a variable and removes it, so no child process inherits a credential.
Stdlib only, no other ddflow layer.
"""

from __future__ import annotations

import contextlib
import os
from typing import Protocol, runtime_checkable

from .ports import port


@port
@runtime_checkable
class Env(Protocol):
    """Process environment. Home: `ddflow.adapters.env` (`SystemEnv`); fake `FakeEnv`."""

    def get(self, name: str, default: str | None = None) -> str | None:
        """The variable's value, or `default` (None unless given) when it is not set; an empty
        value is returned as "", so unset and empty stay apart, as in `os.environ.get`."""

    def snapshot(self) -> dict[str, str]:
        """A copy of every variable now."""

    def take(self, name: str) -> str:
        """The variable's value ("" when not set), and it is gone from the environment."""


class SystemEnv:
    """The default adapter: `os.environ`, read live. Stateless."""

    def get(self, name: str, default: str | None = None) -> str | None:
        return os.environ.get(name, default)

    def snapshot(self) -> dict[str, str]:
        return dict(os.environ)

    def take(self, name: str) -> str:
        value = self.get(name, "") or ""
        with contextlib.suppress(KeyError):
            del os.environ[name]
        return value


#: The one stateless instance callers default to; a test passes `FakeEnv` instead.
DEFAULT = SystemEnv()
