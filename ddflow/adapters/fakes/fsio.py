"""The shipped fake of the `Fsio` port: an in-memory file tree, no disk, no locks held."""

from __future__ import annotations

import contextlib
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..fsio import Fsio
from . import fake


@fake
class FakeFsio(Fsio):
    """Writes land in `files` (path -> text), reads come from it, a lock is a no-op and every
    temporary helper returns a real temp name."""

    def __init__(self, files: dict[str, str] | None = None) -> None:
        self.files = dict(files or {})
        self.calls: list[tuple[str, Path]] = []

    def atomic_write(self, path: Path | str, text: str, *, fsync: bool = True) -> None:
        self.calls.append(("atomic_write", Path(path)))
        self.files[str(path)] = text

    def replace_text(self, path: Path | str, text: str, *, fsync: bool = True) -> None:
        self.calls.append(("replace_text", Path(path)))
        self.files[str(path)] = text

    def file_lock(self, path: Path | str, *, timeout: float | None = None) -> Iterator[Any]:
        self.calls.append(("file_lock", Path(path)))
        return contextlib.nullcontext()

    def scratch_dir(self) -> Any:
        return _ScratchDir()


class _ScratchDir:
    """A `scratch_dir` that yields a `Path` (not `TemporaryDirectory`'s str) and removes it on exit."""

    def __init__(self) -> None:
        self._dir = tempfile.TemporaryDirectory()

    def __enter__(self) -> Path:
        return Path(self._dir.name)

    def __exit__(self, *exc: object) -> None:
        self._dir.cleanup()

    def temp_text(self, text: str, suffix: str = "") -> Iterator[Path]:
        return _TempText(text, suffix)


class _TempText:
    """A `temp_text` that writes to a real temp dir and removes it on exit (no leak)."""

    def __init__(self, text: str, suffix: str) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name) / f"text{suffix}"
        self.path.write_text(text)

    def __enter__(self) -> Path:
        return self.path

    def __exit__(self, *exc: object) -> None:
        self._dir.cleanup()
