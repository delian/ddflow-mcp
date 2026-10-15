"""Reading evidence that lives out of line (B-gap-log-scale).

`full(evidence, root=None)` is what every reader of a stored gate record that needs the BIG values
(a reviewer's findings, a command's output tail) calls: the evidence with its stubs replaced
by the blobs they name. A blob that is missing or does not hash to its address reads as
`Unavailable`, never as an empty value, and `problems` is the doctor's check of the whole log.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..core import blobref as B
from ..core.events import Event
from ..infra.blobstore import BlobStore, known_roots


def fetch(store: BlobStore, ref: str) -> Any:
    """The value ``ref`` names, or `Unavailable` saying why not."""
    raw = store.get_bytes(ref)
    if raw is None:
        return B.Unavailable(ref, "is missing from .ddflow/blobs")
    if B.address(raw) != ref:
        return B.Unavailable(ref, "does not match its address (altered)")
    try:
        return json.loads(raw)
    except ValueError:
        return B.Unavailable(ref, "is not readable")


def full(evidence: Any, root: Path | str | None = None) -> Any:
    """``evidence`` with its out-of-line values read back; anything that is not an evidence
    mapping with stubs in it comes back as it is. ``root`` is the project; without it each
    project this process has opened a log for is tried (a blob is found by its hash)."""
    if not isinstance(evidence, dict) or not any(True for _ in B.stubs(evidence)):
        return evidence
    roots = [Path(root)] if root is not None else known_roots()
    stores = [BlobStore(r) for r in roots]

    def get(ref: str) -> Any:
        worst: Any = B.Unavailable(ref, "is missing from .ddflow/blobs")
        for store in stores:
            got = fetch(store, ref)
            if not isinstance(got, B.Unavailable):
                return got
            if "missing" not in got:  # altered or unreadable outranks missing: keep the tamper signal
                worst = got
        return worst

    return B.join(evidence, get)


def referenced(events: Iterable[Event]) -> dict[str, list[str]]:
    """reference -> ids of the events that carry it."""
    out: dict[str, list[str]] = {}
    for e in events:
        if e.kind in B.KINDS:
            for _key, ref in B.stubs(e.data.get("evidence")):
                out.setdefault(ref, []).append(e.id)
    return out


def problems(root: Path | str, events: Iterable[Event]) -> list[str]:
    """What is wrong with the blobs the log refers to: each missing, altered or malformed one,
    with the events that point at it."""
    store = BlobStore(root)
    found: list[str] = []
    for ref, ids in sorted(referenced(events).items()):
        if not B.hexof(ref):
            found.append(f"{', '.join(ids[:3])}: evidence reference {ref!r} is malformed")
            continue
        got = fetch(store, ref)
        if isinstance(got, B.Unavailable):
            found.append(f"{', '.join(ids[:3])}: {got}")
    return found
