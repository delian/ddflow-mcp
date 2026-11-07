"""`FakeFs`: an in-memory `Fs` over a dict of files, for tests that must not touch a disk.

Paths are normalised to POSIX strings; a directory exists when a file lies under it or it
was made with `mkdir`. `calls` records every operation as `(name, *paths)`. Errors are `OSError` subclasses like
the real adapters (`shutil.Error` where `shutil.move` refuses a taken name or a directory
moved into itself, `shutil.SameFileError` for a file copied onto itself, `FileExistsError`
for a directory over an existing file: callers catch `OSError`); a missing source raises
`FileNotFoundError`; `rmtree` of a missing path raises unless `ignore_errors` (the port's
default). The filesystem root is refused (`ValueError`).
"""

from __future__ import annotations

from pathlib import Path

from ddflow.adapters.fs import Error, SameFileError

from . import fake


def _p(path: Path | str) -> str:
    return Path(path).as_posix().rstrip("/") or "/"


def _no_root(*paths: str) -> None:
    if "/" in paths:
        raise ValueError("FakeFs does not model operations on the filesystem root")


def _exists(fs: FakeFs, p: str) -> bool:
    return p in fs.files or fs.is_dir(p)


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

    def _require_parent(self, p: str, *, allow_missing: bool = False) -> None:
        """Refuse when a parent component of `p` is a file, like the OS, and (unless
        `allow_missing`) when one is missing: the first component that is a file raises
        `NotADirectoryError`; a missing one raises `FileNotFoundError` naming it."""
        prefix, _slash, _rest = p.rpartition("/")
        acc = ""
        for step in prefix.lstrip("/").split("/"):
            acc = f"{acc}/{step}"
            if acc in self.files:
                raise NotADirectoryError(acc)
            if not allow_missing and not self.is_dir(acc):
                raise FileNotFoundError(acc)

    def _refuse_file_ancestor(self, p: str) -> None:
        """Refuse when an ancestor of `p` is a file (`NotADirectoryError`); a
        missing ancestor is fine (its directory is made implicitly)."""
        self._require_parent(p, allow_missing=True)

    def _relocate_dir(self, s: str, d: str) -> None:
        """Move a whole directory `s` (files + subdirs) to `d`, like a rename."""
        for f in self._under(s):
            self.files[d + f[len(s) :]] = self.files.pop(f)
        for x in self._dirs_under(s):
            self.dirs.discard(x)
            self.dirs.add(d + x[len(s) :])
        self.dirs.discard(s)
        self.dirs.discard(d)  # a replaced empty directory gives way to the move
        for acc in self._dir_path(d):
            self.dirs.add(acc)

    @staticmethod
    def _dir_path(d: str) -> list[str]:
        """`d` and every ancestor of `d` (never the root), outer to inner."""
        acc = []
        cur = ""
        for step in d.lstrip("/").split("/"):
            cur = f"{cur}/{step}"
            acc.append(cur)
        return acc

    def copy_file(self, src: Path | str, dst: Path | str) -> None:
        s, d = _p(src), _p(dst)
        self.calls.append(("copy_file", s, d))
        if s not in self.files:
            raise FileNotFoundError(s)
        if self.is_dir(d):  # shutil.copy2 copies INTO an existing directory
            d = self._prefix(d) + s.rsplit("/", 1)[-1]
        if d == s:  # shutil raises SameFileError for a file onto itself
            raise SameFileError(f"{s!r} and {d!r} are the same file")
        if self.is_dir(d):  # the resolved destination is itself a directory
            raise IsADirectoryError(d)
        self._require_parent(d)
        self.files[d] = self.files[s]

    def copy_tree(self, src: Path | str, dst: Path | str, *, dirs_exist_ok: bool = False) -> None:
        s, d = _p(src), _p(dst)
        self.calls.append(("copy_tree", s, d))
        _no_root(s, d)
        if d == s or d.startswith(s + "/"):
            raise ValueError(f"cannot copy {s} into itself ({d})")
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
        _no_root(s, d)
        if self.is_dir(d):  # like shutil.move: into an existing directory
            if d == s:
                return  # moving a path onto itself is a no-op
            d = self._prefix(d) + s.rsplit("/", 1)[-1]
            if _exists(self, d):
                raise Error(f"Destination path {d!r} already exists")
        elif d == s:
            return  # shutil.move of a path onto itself is a no-op
        if s in self.files:
            self._require_parent(d)
            self.files[d] = self.files.pop(s)
            return
        if d.startswith(s + "/"):  # a directory cannot move into its own subtree
            raise Error(f"cannot move a directory into itself ({d})")
        if d in self.files:
            raise FileExistsError(d)  # a directory over an existing file
        # like shutil.move: a directory move falls back to copytree, so a missing
        # parent is created; an ancestor that is a file is refused.
        self._refuse_file_ancestor(d)
        self._relocate_dir(s, d)

    def rmtree(self, path: Path | str, *, ignore_errors: bool = True) -> None:
        d = _p(path)
        self.calls.append(("rmtree", d))
        _no_root(d)
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
        _no_root(s, d)
        if not (s in self.files or self.is_dir(s)):
            raise FileNotFoundError(s)
        if d == s:  # os.replace of a path onto itself is a no-op
            return
        if s in self.files:
            if self.is_dir(d):  # a file over an existing directory
                raise IsADirectoryError(d)
            self._require_parent(d)
            self.files[d] = self.files.pop(s)
            return
        # a directory: os.replace renames it over a new name or an empty one
        self._require_parent(d)  # a missing parent is ENOENT, a file ancestor ENOTDIR
        if d.startswith(s + "/"):  # an ancestor cannot replace a path inside itself (EINVAL)
            raise OSError(f"cannot replace a directory into itself ({d})")
        if d in self.files:  # a directory over an existing file
            raise NotADirectoryError(d)
        if self._under(d) or self._dirs_under(d):
            raise OSError(f"cannot replace the non-empty directory {d!r}")
        self._relocate_dir(s, d)
