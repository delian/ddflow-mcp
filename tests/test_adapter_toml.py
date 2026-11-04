"""The `TomlIo` port: one set of cases against `TomlLibIo` and the shipped `FakeTomlIo`,
plus the real adapter's own promises (files, comments, one error family).

`CONFORMANCE` is read by the generic contract test (tests/test_ports_contract.py).
"""

from __future__ import annotations

import pytest

from ddflow.adapters.fakes.toml import FakeTomlIo
from ddflow.adapters.toml import TomlError, TomlIo, TomlLibIo

SAMPLE = 'name = "x"\nn = 3\nflag = true\nitems = [1, 2]\n\n[a]\nk = "v"\n\n[a.b]\nz = 1\n'


def toml_cases(io) -> None:
    assert isinstance(io, TomlIo)
    data = io.loads(SAMPLE)
    assert data == {"name": "x", "n": 3, "flag": True, "items": [1, 2], "a": {"k": "v", "b": {"z": 1}}}
    assert io.loads("") == {}
    assert io.loads(io.dumps(data)) == data  # write then read gives the data back
    assert io.loads(io.dumps(io.document(SAMPLE))) == data
    for bad in ("not toml", "a = ", "a = 1\na = 2"):
        with pytest.raises(TomlError):
            io.loads(bad)
    with pytest.raises(TomlError):
        io.document("a = ")


CONFORMANCE = {"TomlIo": {"real": lambda monkeypatch: TomlLibIo(), "cases": toml_cases}}


def test_the_default_adapter_passes_the_toml_cases():
    toml_cases(TomlLibIo())


def test_the_fake_passes_the_toml_cases():
    toml_cases(FakeTomlIo())


def test_read_returns_the_file_and_raises_oserror_when_missing(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text(SAMPLE, "utf-8")
    assert TomlLibIo().read(path)["a"]["b"] == {"z": 1}
    assert TomlLibIo().read(str(path)) == TomlLibIo().loads(SAMPLE)
    with pytest.raises(OSError):
        TomlLibIo().read(tmp_path / "missing.toml")
    path.write_text("a = ", "utf-8")
    with pytest.raises(TomlError):
        TomlLibIo().read(path)


def test_the_fake_reads_its_files_and_misses_like_a_file():
    fake = FakeTomlIo({"/x/c.toml": SAMPLE})
    assert fake.read("/x/c.toml")["name"] == "x"
    with pytest.raises(FileNotFoundError):
        fake.read("/x/other.toml")


def test_a_document_edit_keeps_comments_and_layout():
    text = '# top\nname = "x"  # why\n\n[a]\nk = 1\n'
    doc = TomlLibIo().document(text)
    assert TomlLibIo().dumps(doc) == text
    doc["a"]["k"] = 2
    assert TomlLibIo().dumps(doc) == text.replace("k = 1", "k = 2")


def test_the_error_is_a_tomllib_decode_error():
    import tomllib

    assert issubclass(TomlError, tomllib.TOMLDecodeError)
    assert str(TomlError("bad")) == "bad"
    with pytest.raises(TomlError):
        TomlLibIo().document("a = ")
    with pytest.raises(tomllib.TOMLDecodeError):  # what the real `loads` raises
        TomlLibIo().loads("a = ")
