"""The `Fs` port: copy, move, remove-tree and atomic rename of paths.

`shutil` is imported here and nowhere else once the call sites move in (AU-fs-sweep); the
`fsio-home` import contract only lets the sites that still import it shrink. Atomic text
writes, temporary files and locks stay in `ddflow.infra.fsio` (the `Fsio` port), which calls
this adapter for the path operations: one implementation per function (r-adapters).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Protocol, runtime_checkable

from .ports import port


@port
@runtime_checkable
class Fs(Protocol):
    """Path operations on a file tree.

    Home: `ddflow.adapters.fs` (callers reach it through `ddflow.infra.fsio`). Single adapter.
    """

    def copy_file(self, src: Path | str, dst: Path | str) -> None:
        """Copy the file `src` to `dst` with its metadata (like `shutil.copy2`)."""

    def copy_tree(self, src: Path | str, dst: Path | str, *, dirs_exist_ok: bool = False) -> None:
        """Copy the directory tree `src` to `dst`."""

    def move(self, src: Path | str, dst: Path | str) -> None:
        """Move `src` to `dst`, across filesystems if need be (like `shutil.move`)."""

    def rmtree(self, path: Path | str, *, ignore_errors: bool = True) -> None:
        """Remove the directory tree at `path`; errors are ignored by default."""

    def replace_path(self, src: Path | str, dst: Path | str) -> None:
        """Rename `src` over `dst` atomically (same filesystem), like `os.replace`."""


class LocalFs:
    """The default `Fs`: the real filesystem through `shutil` and `os`."""

    def copy_file(self, src: Path | str, dst: Path | str) -> None:
        shutil.copy2(src, dst)

    def copy_tree(self, src: Path | str, dst: Path | str, *, dirs_exist_ok: bool = False) -> None:
        shutil.copytree(src, dst, dirs_exist_ok=dirs_exist_ok)

    def move(self, src: Path | str, dst: Path | str) -> None:
        shutil.move(src, dst)

    def rmtree(self, path: Path | str, *, ignore_errors: bool = True) -> None:
        shutil.rmtree(path, ignore_errors=ignore_errors)

    def replace_path(self, src: Path | str, dst: Path | str) -> None:
        os.replace(src, dst)


#: The shared default instance.
DEFAULT: Fs = LocalFs()
