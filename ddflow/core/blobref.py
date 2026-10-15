"""Out-of-line evidence: the shape of a blob reference and how a payload is split (B-gap-log-scale).

Gate evidence was half the bytes of a real log. A large value under a configured evidence
key is stored once, content-addressed, outside the shard, and the event keeps a stub:

    {"$blob": "sha256:<hex of the value's canonical JSON>", "bytes": <size>}

The event id hashes the stub, and the stub names the digest, so the event still pins the
bytes it refers to. Pure: no I/O. Reading a stub back is the blob store's job
(`infra.blobstore`, `services.logblobs`); a stub whose blob is missing or altered reads as
`Unavailable`, a non-empty marker, never as an empty value.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from typing import Any

from . import digest as D

KEY = "$blob"
ALGO = "sha256"
#: Event kinds whose `evidence` may carry stubs.
KINDS = frozenset(
    {"gate.passed", "gate.failed", "gate.partial", "gate.skipped", "gate.unavailable"}
)


class Unavailable(str):
    """What a stub reads as when its blob cannot be produced. A string, so text that shows it
    shows WHY; truthy, so it is never taken for 'no evidence'; test for it with `isinstance`."""

    digest: str = ""

    def __new__(cls, digest: str, why: str) -> Unavailable:
        self = super().__new__(cls, f"[evidence unavailable: blob {digest} {why}]")
        self.digest = digest
        return self


SHA_HEX = 64
STUB_MAX_KEYS = 2  # the reference key and an optional size


def is_stub(value: Any) -> bool:
    return (
        isinstance(value, dict) and isinstance(value.get(KEY), str) and len(value) <= STUB_MAX_KEYS
    )


def body(value: Any) -> bytes:
    """The bytes a value is stored as (and hashed from)."""
    return D.canonical(value).encode("utf-8")


def address(raw: bytes) -> str:
    return f"{ALGO}:{D.hasher(raw, ALGO).hexdigest()}"


def hexof(ref: str) -> str:
    """The hex part of ``sha256:<hex>``, "" for anything that is not a well-formed reference."""
    algo, _, hexed = ref.partition(":")
    ok = algo == ALGO and len(hexed) == SHA_HEX and all(c in "0123456789abcdef" for c in hexed)
    return hexed if ok else ""


def split(
    data: dict[str, Any], keys: Iterable[str], min_bytes: int
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """``data`` with every big value under ``evidence[key]`` replaced by a stub, and the bodies
    to store (digest -> bytes). ``data`` itself is not changed; unchanged data is returned as is.
    Only a dict `evidence` is touched, and a value that already is a stub stays."""
    ev = data.get("evidence")
    if not isinstance(ev, dict) or min_bytes <= 0:
        return data, {}
    out: dict[str, Any] = {}
    store: dict[str, bytes] = {}
    for key in keys:
        val = ev.get(key)
        if val is None or is_stub(val):
            continue
        raw = body(val)
        if len(raw) < min_bytes:
            continue
        ref = address(raw)
        out[key] = {KEY: ref, "bytes": len(raw)}
        store[ref] = raw
    if not out:
        return data, {}
    return {**data, "evidence": {**ev, **out}}, store


def stubs(evidence: Any) -> Iterator[tuple[str, str]]:
    """(key, reference) of each stub at the top of ``evidence``."""
    if isinstance(evidence, dict):
        for key, val in evidence.items():
            if is_stub(val):
                yield key, val[KEY]


def join(evidence: dict[str, Any], fetch: Callable[[str], Any]) -> dict[str, Any]:
    """A copy of ``evidence`` with each stub replaced by ``fetch(reference)`` (a value, or
    `Unavailable`). When any is unavailable the copy also lists them under
    ``blobs_unavailable`` so a reader that only shows keys cannot miss it."""
    out = dict(evidence)
    missing: list[str] = []
    for key, ref in stubs(evidence):
        got = fetch(ref)
        out[key] = got
        if isinstance(got, Unavailable):
            missing.append(key)
    if missing:
        out["blobs_unavailable"] = sorted(missing)
    return out
