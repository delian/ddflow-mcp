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
        return tempfile.TemporaryDirectory()

    def temp_text(self, text: str, suffix: str = "") -> Iterator[Path]:
        with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False) as handle:
            handle.write(text)
            return _ctx(Path(handle.name))


class _ctx:
    """A one-shot context manager so `temp_text`/`scratch_dir` `yield` a path like the real one."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self) -> Path:
        return self.path

    def __exit__(self, *exc: object) -> None:
        return None
