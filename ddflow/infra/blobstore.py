"""The content-addressed blob store under `.ddflow/blobs/` (B-gap-log-scale).

One file per blob, named by its sha256 and fanned out by the first two hex digits. Blobs are
immutable and are committed with the log (`services.eventcommit`). Every file operation goes
through `fsio`, and the class is the only thing callers touch, so a different backend
(the planned storage seam, D-storage) replaces it without touching a caller.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..core import blobref as B
from . import fsio

DIR = Path(".ddflow") / "blobs"


#: Projects this process has opened a log for. A stub names its blob by content, not by place,
#: and a blob is found by its hash wherever it lies, so a reader holding only a record (it
#: has no log in hand) looks in each. Never trusted: `services.logblobs.fetch` re-hashes.
_KNOWN_ROOTS: list[Path] = []


def remember(root: Path | str) -> None:
    path = Path(root)
    if path not in _KNOWN_ROOTS:
        _KNOWN_ROOTS.append(path)


def known_roots() -> list[Path]:
    return list(_KNOWN_ROOTS)


class BlobStore:
    def __init__(self, root: Path | str) -> None:
        self.dir = Path(root) / DIR

    def path(self, ref: str) -> Path | None:
        hexed = B.hexof(ref)
        return self.dir / hexed[:2] / f"{hexed}.json" if hexed else None

    def put(self, ref: str, raw: bytes) -> None:
        """Store ``raw`` under ``ref`` unless an intact blob is already there."""
        path = self.path(ref)
        if path is None or B.address(raw) != ref:
            raise ValueError(f"{ref!r} is not the address of these bytes")
        if self.get_bytes(ref) == raw:
            return
        fsio.atomic_write(path, raw, mode=0o644)

    def get_bytes(self, ref: str) -> bytes | None:
        path = self.path(ref)
        if path is None:
            return None
        try:
            return path.read_bytes()
        except OSError:
            return None

    def refs(self) -> Iterator[str]:
        """The reference of every blob file present (whatever its content)."""
        if not self.dir.is_dir():
            return
        for f in sorted(self.dir.glob("??/*.json")):
            yield f"{B.ALGO}:{f.stem}"

    def remove(self, ref: str) -> None:
        path = self.path(ref)
        if path is not None:
            with contextlib.suppress(OSError):
                path.unlink()


def externalize(root: Path | str, log_cfg: Any, kind: str, data: dict[str, Any]) -> dict[str, Any]:
    """``data`` for an event of ``kind`` with its large evidence stored out of line (the
    `[log].evidence_blob*` knobs of ``cfg``); the bodies are on disk before the event that
    names them is written. Everything else comes back unchanged."""
    if kind not in B.KINDS or not log_cfg.evidence_blobs:
        return data
    new, bodies = B.split(data, log_cfg.evidence_blob_keys, log_cfg.evidence_blob_min_bytes)
    if bodies:
        store = BlobStore(root)
        for ref, raw in bodies.items():
            store.put(ref, raw)
    return new
