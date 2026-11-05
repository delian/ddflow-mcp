"""The `Fsio` port: atomic writes, temporary files and locks.

Home of the port, and (as the family moves in, ADP-migrate-homes) of its default adapter.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .ports import port


@port
@runtime_checkable
class Fsio(Protocol):
    """Atomic writes, temporary files and locks in one home.

    Home: `ddflow.adapters.fsio` (compat re-export `ddflow.infra.fsio`). Single adapter.
    """

    def atomic_write(self, path: Path | str, text: str, *, fsync: bool = True) -> None:
        """Write `text` all-or-nothing, beside the target then renamed over it."""

    def replace_text(self, path: Path | str, text: str, *, fsync: bool = True) -> None:
        """Rewrite `path` in place only when its content actually changed."""

    def file_lock(self, path: Path | str, *, timeout: float | None = None) -> Iterator[Any]:
        """An exclusive lock for `path`, released on exit; raises `LockTimeout`."""

    def scratch_dir(self) -> Any:
        """A temporary directory that removes itself on exit."""

    def temp_text(self, text: str, suffix: str = "") -> Iterator[Path]:
        """`text` in a temporary file, removed on exit."""
