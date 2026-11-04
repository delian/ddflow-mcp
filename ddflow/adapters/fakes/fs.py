"""`FakeFs`: an in-memory `Fs` over a dict of files, for tests that must not touch a disk.

Paths are normalised to POSIX strings; a directory exists when a file lies under it or it
was made with `mkdir`. `calls` records every operation as `(name, *paths)`. Errors mirror the
real ones: a missing source raises `FileNotFoundError`; `rmtree` of a missing path raises
unless `ignore_errors` (the default).
"""

from __future__ import annotations

from pathlib import Path

from . import fake


def _p(path: Path | str) -> str:
    return Path(path).as_posix().rstrip("/") or "/"


@fake
class FakeFs:
    """An `Fs` holding `files` (path -> bytes) and `dirs` in memory."""

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files: dict[str, bytes] = {_p(k): v for k, v in (files or {}).items()}
        self.dirs: set[str] = set()
        self.calls: list[tuple[str, ...]] = []

    def mkdir(self, path: Path | str) -> None:
        self.dirs.add(_p(path))

    def is_dir(self, path: Path | str) -> bool:
        d = _p(path)
        return d in self.dirs or any(f.startswith(d + "/") for f in self.files)

    def _under(self, root: str) -> list[str]:
        return sorted(f for f in self.files if f.startswith(root + "/"))

    def copy_file(self, src: Path | str, dst: Path | str) -> None:
        s, d = _p(src), _p(dst)
        self.calls.append(("copy_file", s, d))
        if s not in self.files:
            raise FileNotFoundError(s)
        self.files[d] = self.files[s]

    def copy_tree(self, src: Path | str, dst: Path | str, *, dirs_exist_ok: bool = False) -> None:
        s, d = _p(src), _p(dst)
        self.calls.append(("copy_tree", s, d))
        if not self.is_dir(s):
            raise FileNotFoundError(s)
        if self.is_dir(d) and not dirs_exist_ok:
            raise FileExistsError(d)
        for f in self._under(s):
            self.files[d + f[len(s) :]] = self.files[f]
        self.dirs.add(d)

    def move(self, src: Path | str, dst: Path | str) -> None:
        s, d = _p(src), _p(dst)
        self.calls.append(("move", s, d))
        if s in self.files:
            self.files[d] = self.files.pop(s)
        elif self.is_dir(s):
            for f in self._under(s):
                self.files[d + f[len(s) :]] = self.files.pop(f)
            self.dirs.discard(s)
            self.dirs.add(d)
        else:
            raise FileNotFoundError(s)

    def rmtree(self, path: Path | str, *, ignore_errors: bool = True) -> None:
        d = _p(path)
        self.calls.append(("rmtree", d))
        if not self.is_dir(d):
            if ignore_errors:
                return
            raise FileNotFoundError(d)
        for f in self._under(d):
            del self.files[f]
        self.dirs = {x for x in self.dirs if x != d and not x.startswith(d + "/")}

    def replace_path(self, src: Path | str, dst: Path | str) -> None:
        s, d = _p(src), _p(dst)
        self.calls.append(("replace_path", s, d))
        if s not in self.files:
            raise FileNotFoundError(s)
        self.files[d] = self.files.pop(s)
