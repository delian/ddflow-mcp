"""The `ManagedFile` port: one set of cases against `JsonManagedFile` and the shipped
`FakeManagedFile` (merge owns only ddflow's keys, remove cleans up, an unmanaged key is never
touched), plus the real adapter's file promise.

`CONFORMANCE` is read by the generic contract test (tests/test_ports_contract.py).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ddflow.adapters.fakes.managed_files import FakeManagedFile
from ddflow.adapters.managed_files import OWNER_KEY, JsonManagedFile, ManagedFile


def _doc(io, path):
    return json.loads(Path(path).read_text() if isinstance(io, JsonManagedFile) else io.files[path])


def config_cases(io) -> None:
    assert isinstance(io, ManagedFile)
    root = Path(tempfile.mkdtemp())
    (root / "settings.json").write_text('{"user": {"x": 1}}\n', "utf-8")
    path = str(root / "settings.json") if isinstance(io, JsonManagedFile) else "settings.json"
    if not isinstance(io, JsonManagedFile):
        io.files["settings.json"] = '{"user": {"x": 1}}\n'
    io.merge(path, owned={"mcpServers": {"a": {"command": "x"}}}, version="0.2.10")
    doc = _doc(io, path)
    # ddflow's key is merged; the marker records it
    assert doc["mcpServers"] == {"a": {"command": "x"}}
    assert doc[OWNER_KEY]["keys"] == ["mcpServers"]
    # the user's own key is untouched and unowned
    assert doc["user"] == {"x": 1}
    assert io.owned(path) == {"mcpServers": "current"}
    io.remove(path, keys=frozenset({"mcpServers"}))
    after = _doc(io, path)
    assert "mcpServers" not in after and OWNER_KEY not in after and after["user"] == {"x": 1}


CONFORMANCE = {
    "ManagedFile": {
        "real": lambda monkeypatch: JsonManagedFile(),
        "cases": config_cases,
        "fake": lambda cls: cls({"settings.json": '{"user": {"x": 1}}\n'}),
    }
}


def _root(tmp_path):
    (tmp_path / "settings.json").write_text('{"user": {"x": 1}}\n', "utf-8")
    return str(tmp_path / "settings.json")


def test_the_default_adapter_passes_the_managed_file_cases():
    config_cases(JsonManagedFile())


def test_the_fake_passes_the_managed_file_cases():
    config_cases(FakeManagedFile())


def test_merge_is_idempotent(tmp_path):
    path = _root(tmp_path)
    io = JsonManagedFile()
    io.merge(path, owned={"mcpServers": {"a": {"command": "x"}}}, version="0.2.10")
    first = Path(path).read_text()
    io.merge(path, owned={"mcpServers": {"a": {"command": "x"}}}, version="0.2.10")
    assert Path(path).read_text() == first
