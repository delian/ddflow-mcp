"""`FakeEntropy`: deterministic `Entropy`. Same seed, same tokens, ids and jitter, every run."""

from __future__ import annotations

import random
import uuid

from . import fake


@fake
class FakeEntropy:
    """Everything is drawn from `random.Random(seed)`; nothing reads the OS."""

    def __init__(self, seed: int = 0) -> None:
        self._rng = random.Random(seed)

    def token_hex(self, nbytes: int = 8) -> str:
        return self._rng.randbytes(nbytes).hex()

    def uuid4(self) -> str:
        return str(uuid.UUID(bytes=self._rng.randbytes(16), version=4))

    def uniform(self, low: float, high: float) -> float:
        return self._rng.uniform(low, high)
