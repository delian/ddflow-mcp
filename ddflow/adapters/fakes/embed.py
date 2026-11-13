"""`FakeEmbed`: a scripted `Embed` that needs no model2vec. Its default vector is `(0.0,)`."""

from __future__ import annotations

from collections.abc import Sequence

from . import fake


@fake
class FakeEmbed:
    """Returns the one vector it was built with for every text; defaults to `(0.0,)`."""

    def __init__(self, vector: tuple[float, ...] = (0.0,)) -> None:
        self._vector = vector

    def embed(self, model_dir: str, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._vector for _ in texts)
