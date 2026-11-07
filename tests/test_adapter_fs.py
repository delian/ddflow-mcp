"""The `Fs` port: the default adapter's behaviour and the fake's (CONFORMANCE)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from ddflow.adapters import fs as F
from ddflow.adapters.fakes import FAKES
from ddflow.adapters.fakes.fs import FakeFs
from ddflow.adapters.fs import Fs
from ddflow.infra import fsio


class _Disk:
    """The real adapter over a tmp dir, with the same read helpers the fake has."""

    def __init__(self, root: Path) -> None:
        self.root, self.fs = root, F.LocalFs()

    def __getattr__(self, name: str):
        return getattr(self.fs, name)

    def put(self, rel: str, data: bytes) -> str:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return str(p)

    def path(self, rel: str) -> str:
        return str(self.root / rel)

    def read(self, rel: str) -> bytes | None:
        p = self.root / rel
        return p.read_bytes() if p.is_file() else None

    def exists_dir(self, rel: str) -> bool:
        return (self.root / rel).is_dir()


class _Mem:
    def __init__(self, fs: FakeFs | None = None) -> None:
        self.fs = fs or FakeFs()

    def __getattr__(self, name: str):
        return getattr(self.fs, name)

    def put(self, rel: str, data: bytes) -> str:
        self.fs.files[f"/m/{rel}"] = data
        return f"/m/{rel}"

    def path(self, rel: str) -> str:
        return f"/m/{rel}"

    def read(self, rel: str) -> bytes | None:
        return self.fs.files.get(f"/m/{rel}")

    def exists_dir(self, rel: str) -> bool:
        return self.fs.is_dir(f"/m/{rel}")


def fs_conformance(adapter) -> None:
    """What every Fs does, real or fake (the harness adds put/read over the adapter)."""
    h = _Mem(adapter) if isinstance(adapter, FakeFs) else adapter
    a = h.put("a/one.txt", b"1")
    h.put("a/sub/two.txt", b"2")
    h.copy_file(a, h.path("copy.txt"))
    assert h.read("copy.txt") == b"1" and h.read("a/one.txt") == b"1"
    h.copy_tree(h.path("a"), h.path("b"))
    assert h.read("b/sub/two.txt") == b"2"
    with pytest.raises(FileExistsError):
        h.copy_tree(h.path("a"), h.path("b"))
    h.copy_tree(h.path("a"), h.path("b"), dirs_exist_ok=True)
    h.move(h.path("copy.txt"), h.path("moved.txt"))
    assert h.read("moved.txt") == b"1" and h.read("copy.txt") is None
    h.replace_path(h.path("moved.txt"), h.path("a/one.txt"))
    assert h.read("a/one.txt") == b"1" and h.read("moved.txt") is None
    h.rmtree(h.path("b"))
    assert not h.exists_dir("b")
    h.rmtree(h.path("b"))  # missing: ignored by default
    with pytest.raises(FileNotFoundError):
        h.rmtree(h.path("b"), ignore_errors=False)
    with pytest.raises(FileNotFoundError):
        h.copy_file(h.path("nope"), h.path("x"))


def _real(monkeypatch) -> _Disk:
    root = fsio.unused_dir("fs-conf-")
    root.mkdir()
    return _Disk(root)


CONFORMANCE = {"Fs": {"real": _real, "cases": fs_conformance}}


def test_the_adapter_and_the_fake_satisfy_the_port():
    assert isinstance(F.LocalFs(), Fs) and isinstance(FakeFs(), Fs)
    assert FAKES["Fs"] is FakeFs and isinstance(F.DEFAULT, F.LocalFs)


def test_real_adapter_conforms(tmp_path):
    fs_conformance(_Disk(tmp_path))


def test_fake_conforms():
    fs_conformance(_Mem())


def test_fake_records_calls_and_moves_directories():
    f = FakeFs({"/r/x/a": b"A", "/r/x/b/c": b"C"})
    f.move("/r/x", "/r/y")
    assert f.files == {"/r/y/a": b"A", "/r/y/b/c": b"C"}
    assert f.calls == [("move", "/r/x", "/r/y")]


def test_fsio_path_operations_go_through_the_one_adapter(monkeypatch, tmp_path):
    fake = FakeFs({"/s/f": b"1", "/s/d/g": b"2"})
    monkeypatch.setattr(F, "DEFAULT", fake)
    fsio.copy_file("/s/f", "/t/f")
    fsio.copy_tree("/s/d", "/t/d")
    fsio.move("/s/f", "/t/m")
    fsio.rmtree("/s/d")
    fsio.replace_path("/t/m", "/t/n")
    assert [c[0] for c in fake.calls] == [
        "copy_file",
        "copy_tree",
        "move",
        "rmtree",
        "replace_path",
    ]


def test_fsio_real_roundtrip(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "f").write_text("x")
    fsio.copy_tree(tmp_path / "d", tmp_path / "e")
    fsio.move(tmp_path / "e", tmp_path / "g")
    assert (tmp_path / "g" / "f").read_text() == "x"
    fsio.rmtree(tmp_path / "g")
    assert not (tmp_path / "g").exists()


def test_fake_copy_file_mirrors_local_on_directories_and_parents():
    # copy_file onto an existing directory copies INTO it (shutil.copy2), not at the path
    f = FakeFs({"/src/f": b"1"})
    f.mkdir("/dst")
    f.copy_file("/src/f", "/dst")
    assert "/dst" not in f.files and f.files["/dst/f"] == b"1"
    # copying a file onto itself is not silent: shutil raises SameFileError
    with pytest.raises(shutil.SameFileError):
        FakeFs({"/f": b"1"}).copy_file("/f", "/f")
    # a missing parent is refused, not invented
    with pytest.raises(FileNotFoundError):
        FakeFs({"/src/f": b"1"}).copy_file("/src/f", "/no/parent/f")
    # a parent that is a file, not a directory, is refused like the real adapters
    with pytest.raises(NotADirectoryError):
        FakeFs({"/src/f": b"1", "/p": b"x"}).copy_file("/src/f", "/p/child")
    # a destination directory that already holds that name as a sub-directory is refused
    f2 = FakeFs({"/f": b"1"})
    f2.mkdir("/dst")
    f2.mkdir("/dst/f")
    with pytest.raises(IsADirectoryError):
        f2.copy_file("/f", "/dst")


def test_fake_move_mirrors_local_on_parents_and_self():
    # a missing parent is refused
    with pytest.raises(FileNotFoundError):
        FakeFs({"/src/f": b"1"}).move("/src/f", "/no/parent/f")
    # move(x, x) is a no-op, like shutil.move
    f = FakeFs({"/f": b"1"})
    f.move("/f", "/f")
    assert f.files == {"/f": b"1"}


def test_fake_replace_path_mirrors_local_on_directories():
    # replacing (renaming) a directory works
    f = FakeFs({"/dir/f": b"1"})
    f.replace_path("/dir", "/renamed")
    assert f.files == {"/renamed/f": b"1"} and not f.is_dir("/dir") and f.is_dir("/renamed")
    # a file over an existing directory is refused, like os.replace
    f2 = FakeFs({"/f": b"1"})
    f2.mkdir("/d")
    f2.files["/d/keep"] = b"k"
    with pytest.raises(IsADirectoryError):
        f2.replace_path("/f", "/d")
    # a directory over an existing file is refused, like os.replace
    f3 = FakeFs({"/file": b"x"})
    f3.mkdir("/dir")
    f3.files["/dir/inner"] = b"i"
    with pytest.raises(NotADirectoryError):
        f3.replace_path("/dir", "/file")
    # a directory into its own descendant is refused, like os.replace (a bare
    # OSError: Python has no subclass for EINVAL)
    with pytest.raises(OSError) as _exc:
        FakeFs({"/a/f": b"1"}).replace_path("/a", "/a/child")
    assert type(_exc.value) is OSError


def test_fake_mirrors_shutil_on_directories():
    f = FakeFs({"/a/f": b"1", "/file": b"x", "/dst": b"old"})
    f.mkdir("/a/empty")
    with pytest.raises(FileExistsError):
        f.copy_tree("/a", "/dst")  # destination is a file
    f.copy_tree("/a", "/c")
    assert f.is_dir("/c/empty") and f.files["/c/f"] == b"1"
    f.move("/file", "/c")  # into an existing directory
    assert f.files["/c/file"] == b"x"
    f.move("/a", "/z")
    assert not f.is_dir("/a/empty") and f.is_dir("/z/empty")
    f.files["/c/g"] = b"g"
    f.mkdir("/dd")
    f.files["/dd/g"] = b"old"
    with pytest.raises(FileExistsError):
        f.move("/dd/g", "/dd")  # shutil.move refuses: the target name exists
    with pytest.raises(FileExistsError):
        f.move("/c/g", "/dd")  # the name is taken inside the directory
    with pytest.raises(FileExistsError):
        f.move("/z", "/dst")  # a directory over a file
    with pytest.raises(ValueError):
        f.copy_tree("/z", "/z/sub", dirs_exist_ok=True)
    for call in (lambda: f.rmtree("/"), lambda: f.copy_tree("/", "/x"), lambda: f.move("/", "/x")):
        with pytest.raises(ValueError):
            call()
