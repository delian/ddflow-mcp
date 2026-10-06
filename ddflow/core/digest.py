"""Content digests: the one place ddflow hashes text or bytes.

Pure (stdlib `hashlib` only). A digest that is STORED -- in the event log, a ledger, a
generated file's header -- keeps the algorithm and length it was written with, so callers
name both; a mismatch would read every old record as changed. The architecture guards
count `hashlib` outside this module and only let the count go down (D-unify 4).
"""

from __future__ import annotations

import hashlib


def digest(
    data: str | bytes, algo: str = "sha256", *, length: int | None = None, errors: str = "strict"
) -> str:
    """The hex digest of `data` (text is UTF-8 encoded with `errors`), cut to `length`
    characters when given."""
    raw = data.encode("utf-8", errors) if isinstance(data, str) else data
    hexed = hashlib.new(algo, raw).hexdigest()
    return hexed[:length] if length is not None else hexed
