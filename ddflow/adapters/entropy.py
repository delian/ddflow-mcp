"""The `Entropy` port and its default adapter: random numbers, tokens and ids.

One home for `random`, `secrets` and `uuid`, so an id or a jittered delay can be made
reproducible in a test. `token_hex` and `uuid4` are for ids and are unpredictable by default
(`secrets`, `uuid`); `uniform` is for jitter and is not for secrets. Stdlib only.
"""

from __future__ import annotations

import random
import secrets
import uuid
from typing import Protocol, runtime_checkable

from .ports import port


@port
@runtime_checkable
class Entropy(Protocol):
    """Randomness and ids. Home: `ddflow.adapters.entropy` (`SystemEntropy`); fake `FakeEntropy`."""

    def token_hex(self, nbytes: int = 8) -> str:
        """`2 * nbytes` hex characters."""

    def uuid4(self) -> str:
        """A canonical 36-character UUID string."""

    def uniform(self, low: float, high: float) -> float:
        """A float in `[low, high]` (jitter; not for secrets)."""


class SystemEntropy:
    """The default adapter: `secrets`, `uuid` and `random`. Stateless."""

    def token_hex(self, nbytes: int = 8) -> str:
        return secrets.token_hex(nbytes)

    def uuid4(self) -> str:
        return str(uuid.uuid4())

    def uniform(self, low: float, high: float) -> float:
        return random.uniform(low, high)


#: The one stateless instance callers default to; a test passes `FakeEntropy` instead.
DEFAULT = SystemEntropy()


def default_entropy() -> SystemEntropy:
    """The source a caller uses when it was handed none."""
    return DEFAULT
