"""The `Digest` port: the one hashing and content-digest family.

Home of the port, and (as the family moves in, ADP-migrate-homes) of its default adapter.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .ports import port


@port
@runtime_checkable
class Digest(Protocol):
    """The one hashing and content-digest family, stdlib `hashlib` only.

    Home: `ddflow.adapters.digest` (compat re-export `ddflow.core.digest`). Single adapter;
    `hashlib` is imported here alone, and the architecture guards only let the count go down.
    """

    def content_digest(
        self,
        data: str | bytes | memoryview,
        algo: str = "sha256",
        *,
        length: int | None = None,
        errors: str = "strict",
        size: int | None = None,
    ) -> str:
        """The hex digest of `data`, cut to `length` characters when given."""

    def canonical(self, obj: Any) -> str:
        """Stable JSON (sorted keys, no spaces): the input to every value digest."""

    def of_obj(
        self, obj: Any, algo: str = "blake2b", *, size: int | None = 12, length: int | None = None
    ) -> str:
        """The digest of `obj`'s `canonical` form."""
