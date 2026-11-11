"""The `Platform` port: the operations that differ by operating system.

fcntl flocks, process groups and their signals, 0600 permissions, home/config
directories, executable lookup and terminal detection sit in a few homes (fsio, proc,
notify). One `Platform` port so a Windows or macOS difference is ONE adapter instead of a
branch in four modules. The Posix adapter is the existing code unchanged; the Windows
adapter reports `unavailable` (D-adapters-answers-3: Linux and macOS supported, Windows
documented unsupported).

Home: `ddflow.adapters.platform` (the seam; the code each verb wraps stays in its home).
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from . import fs, fsio
from . import proc as _proc
from .ports import port


@port
@runtime_checkable
class Platform(Protocol):
    """OS-dependent operations as one seam, the existing code as the default adapter.

    Home: `ddflow.adapters.platform`.
    """

    def file_lock(self, path: Path | str, **kwargs: Any) -> Iterator[None]:
        """Hold an exclusive advisory lock on ``path`` for the block (see `fsio.file_lock`)."""

    def kill_group(self, proc: Any, sig: int | None = None) -> None:
        """Signal ``proc`` and everything it started, its whole process group."""

    def which(self, cmd: str) -> str | None:
        """The first executable named ``cmd`` on PATH; None when there is none."""

    def home_dir(self) -> Path:
        """The current user's home directory."""

    def chmod_private(self, path: Path | str) -> None:
        """Restrict ``path`` to its owner (0600)."""

    def is_terminal(self, stream: Any) -> bool:
        """Is ``stream`` an interactive terminal?"""


class Posix:
    """The default `Platform`: the existing POSIX code, unchanged.

    ``file_lock`` is `fsio.file_lock`, ``kill_group`` is `proc.kill_group`,
    ``which`` is `fs.which`; home and 0600 are `pathlib`/`os`; ``is_terminal`` is
    `stream.isatty()`.
    """

    def file_lock(self, path: Path | str, **kwargs: Any) -> Iterator[None]:
        return fsio.file_lock(path, **kwargs)

    def kill_group(self, proc: Any, sig: int | None = None) -> None:
        _proc.kill_group(proc, sig)

    def which(self, cmd: str) -> str | None:
        return fs.which(cmd)

    def home_dir(self) -> Path:
        return Path.home()

    def chmod_private(self, path: Path | str) -> None:
        os.chmod(path, 0o600)

    def is_terminal(self, stream: Any) -> bool:
        return bool(stream is not None and hasattr(stream, "isatty") and stream.isatty())


class Windows:
    """The Windows adapter: documented unsupported, every verb reports `unavailable`.

    Nothing here runs on Windows (D-adapters-answers-3, 2026-11-01); each call raises
    `NotImplementedError` naming the verb, so a caller cannot take a Windows answer for a
    silently-empty POSIX one.
    """

    @staticmethod
    def _refuse(verb: str) -> None:
        raise NotImplementedError(f"Platform.{verb} is unavailable on Windows (unsupported)")

    def file_lock(self, path: Path | str, **kwargs: Any) -> Iterator[None]:
        self._refuse("file_lock")
        return _yield()

    def kill_group(self, proc: Any, sig: int | None = None) -> None:
        self._refuse("kill_group")

    def which(self, cmd: str) -> str | None:
        self._refuse("which")

    def home_dir(self) -> Path:
        self._refuse("home_dir")

    def chmod_private(self, path: Path | str) -> None:
        self._refuse("chmod_private")

    def is_terminal(self, stream: Any) -> bool:
        self._refuse("is_terminal")


@contextlib.contextmanager
def _yield() -> Iterator[None]:
    """The empty block for a Windows `file_lock` refusal (the raise happened up front)."""
    yield


#: The default on every supported platform.
DEFAULT: Platform = Posix()
