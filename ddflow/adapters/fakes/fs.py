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
        d = _p(path)
        self.calls.append(("mkdir", d))
        self.dirs.add(d)

    @staticmethod
    def _prefix(d: str) -> str:
        return d if d.endswith("/") else d + "/"

    def is_dir(self, path: Path | str) -> bool:
        d = _p(path)
        pre = self._prefix(d)
        return d in self.dirs or any(f.startswith(pre) for f in self.files)

    def _under(self, root: str) -> list[str]:
        pre = self._prefix(root)
        return sorted(f for f in self.files if f.startswith(pre))

    def _dirs_under(self, root: str) -> list[str]:
        pre = self._prefix(root)
        return sorted(x for x in self.dirs if x.startswith(pre))

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
        if d in self.files or (self.is_dir(d) and not dirs_exist_ok):
            raise FileExistsError(d)
        for f in self._under(s):
            self.files[d + f[len(s) :]] = self.files[f]
        for x in self._dirs_under(s):
            self.dirs.add(d + x[len(s) :])
        self.dirs.add(d)

    def move(self, src: Path | str, dst: Path | str) -> None:
        s, d = _p(src), _p(dst)
        self.calls.append(("move", s, d))
        if not (s in self.files or self.is_dir(s)):
            raise FileNotFoundError(s)
        if self.is_dir(d):  # like shutil.move: into an existing directory
            d = self._prefix(d) + s.rsplit("/", 1)[-1]
        if s in self.files:
            self.files[d] = self.files.pop(s)
            return
        for f in self._under(s):
            self.files[d + f[len(s) :]] = self.files.pop(f)
        for x in self._dirs_under(s):
            self.dirs.discard(x)
            self.dirs.add(d + x[len(s) :])
        self.dirs.discard(s)
        self.dirs.add(d)

    def rmtree(self, path: Path | str, *, ignore_errors: bool = True) -> None:
        d = _p(path)
        self.calls.append(("rmtree", d))
        if not self.is_dir(d):
            if ignore_errors:
                return
            raise FileNotFoundError(d)
        for f in self._under(d):
            del self.files[f]
        self.dirs = {x for x in self.dirs if x != d and not x.startswith(self._prefix(d))}

    def replace_path(self, src: Path | str, dst: Path | str) -> None:
        s, d = _p(src), _p(dst)
        self.calls.append(("replace_path", s, d))
        if s not in self.files:
            raise FileNotFoundError(s)
        self.files[d] = self.files.pop(s)
