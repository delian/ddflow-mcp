"""The `Fsio` port: the same cases against `FakeFsio` (in-memory) and the real module
functions (`ddflow.adapters.fsio`).

`CONFORMANCE` is read by the generic contract test (tests/test_ports_contract.py).
"""

from __future__ import annotations

from pathlib import Path

import tempfile

from ddflow.adapters import fsio as real_fsio
from ddflow.adapters.fakes.fsio import FakeFsio


def fsio_cases(io) -> None:
    path = str(Path(tempfile.mkdtemp()) / "f.txt")
    assert io.atomic_write(path, "hello") is None
    with io.file_lock(path):
        pass
    with io.temp_text("x", suffix=".txt") as p:
        p.write_text("x")


CONFORMANCE = {
    "Fsio": {
        "real": lambda monkeypatch: real_fsio,
        "cases": fsio_cases,
        "fake": lambda cls: cls(),
    }
}


def test_the_fake_writes_to_memory():
    io = FakeFsio()
    io.atomic_write("a/x", "one")
    assert io.files["a/x"] == "one"
    assert io.calls == [("atomic_write", Path("a/x"))]
