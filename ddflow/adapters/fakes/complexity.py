"""`FakeComplexity`: a scripted `Complexity` that needs no radon."""

from __future__ import annotations

from . import fake


@fake
class FakeComplexity:
    """Returns the table it was built with, whatever the source; defaults to empty."""

    def __init__(self, found: dict[str, tuple[int, int]] | None = None) -> None:
        self._found = dict(found or {})

    def complexities(self, source: bytes | None) -> dict[str, tuple[int, int]]:
        return dict(self._found)
