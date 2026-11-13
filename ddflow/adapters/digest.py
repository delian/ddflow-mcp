"""The `Digest` port: the one hashing and content-digest family.

Pure (stdlib `hashlib` only): the default adapter is the module's own functions (`hasher`,
`content_digest`, `canonical`, `of_obj`), the same implementation `ddflow.core.digest`
re-exports for compatibility until its import-linter baseline is empty (ADP-migrate-homes).
A digest that is STORED -- in the event log, a ledger, a generated file's header -- keeps the
algorithm and length it was written with, so callers name both; a mismatch would read every
old record as changed. The architecture guards count `hashlib` outside this module and only
let the count go down (D-unify 4).
"""

from __future__ import annotations

import hashlib
import json
import re
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


#: Algorithms used for identity, never for security (git's sha1 blob ids, short ids):
#: hashed with ``usedforsecurity=False`` so a FIPS build does not refuse them.
NOT_FOR_SECURITY = frozenset({"sha1", "md5"})


def hasher(data: bytes | memoryview = b"", algo: str = "sha256", *, size: int | None = None):
    """An incremental hash object (`update`, `hexdigest`, `copy`), seeded with `data`: for
    a digest computed in pieces or continued as a file grows (the event log's shards).
    ``size`` is blake2's own ``digest_size`` (a different hash from a cut-down long one)."""
    kwargs: dict = {"digest_size": size} if size is not None else {}
    if algo in NOT_FOR_SECURITY:
        kwargs["usedforsecurity"] = False
    return hashlib.new(algo, data, **kwargs)


#: A redaction marker in any spelling: the `[REDACTED:<kind>]` of a marker-style profile and
#: the bare `[REDACTED]` of a masking one. The grammar `core.redact` strips markers with; the
#: closed `[REDACTED:<kind>]` form `core.textcut` keeps whole is pinned to what the redactor
#: writes by `tests/test_compat_digests.py`.
MARKER = re.compile(r"\[REDACTED(?::[a-z0-9_]*)?\]?")
#: The one token every spelling of a redaction marker is mapped to before a digest.
REDACTED = "[REDACTED]"


def canonical(obj: Any) -> str:
    """Stable JSON: sorted keys, no spaces. The input to every content hash of a value."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def of_obj(
    obj: Any, algo: str = "blake2b", *, size: int | None = 12, length: int | None = None
) -> str:
    """The digest of ``obj``'s `canonical` form: the ONE way a value (not a text) is
    fingerprinted. ``size`` is blake2's ``digest_size`` (bytes; None for ``algo`` that has no
    such setting), ``length`` cuts the hex string."""
    return content_digest(
        canonical(obj), algo, size=size if algo == "blake2b" else None, length=length
    )


def normalize_markers(text: str) -> str:
    """``text`` with every redaction marker spelled `[REDACTED]`: clones on different
    redaction profiles write different markers for the same secret, so a digest that decides
    whether two records are the same must not see the difference."""
    return MARKER.sub(REDACTED, text)


def normalize_text(text: str) -> str:
    """``text`` as a duplicate check compares it: markers unified, case folded, whitespace
    runs one space."""
    return " ".join(normalize_markers(text).casefold().split())


def normalized(obj: Any) -> Any:
    """``obj`` with `normalize_markers` applied to every string in it (keys included); its
    shape and every other value are unchanged."""
    if isinstance(obj, str):
        return normalize_markers(obj)
    if isinstance(obj, dict):
        return {
            (normalize_markers(k) if isinstance(k, str) else k): normalized(v)
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [normalized(v) for v in obj]
    return obj


def content_digest(
    data: str | bytes | memoryview,
    algo: str = "sha256",
    *,
    length: int | None = None,
    errors: str = "strict",
    size: int | None = None,
) -> str:
    """The hex digest of `data` (text is UTF-8 encoded with `errors`), cut to `length`
    characters when given; ``size`` is blake2's ``digest_size`` (see `hasher`)."""
    raw = data.encode("utf-8", errors) if isinstance(data, str) else data
    hexed = hasher(raw, algo, size=size).hexdigest()
    return hexed[:length] if length is not None else hexed
