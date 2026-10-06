"""Content digests: where ddflow's hashing of text or bytes moves to (B-uni-fsio-digest).

Pure (stdlib `hashlib` only). A digest that is STORED -- in the event log, a ledger, a
generated file's header -- keeps the algorithm and length it was written with, so callers
name both; a mismatch would read every old record as changed. The architecture guards
count `hashlib` outside this module and only let the count go down (D-unify 4).
"""

from __future__ import annotations

import hashlib

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
