"""The `TomlIo` port: one set of cases against `TomlLibIo` and the shipped `FakeTomlIo`,
plus the real adapter's own promises (files, one error family).

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
    assert data == {
        "name": "x",
        "n": 3,
        "flag": True,
        "items": [1, 2],
        "a": {"k": "v", "b": {"z": 1}},
    }
    assert io.loads("") == {}
    for bad in ("not toml", "a = ", "a = 1\na = 2"):
        with pytest.raises(TomlError):
            io.loads(bad)
    # write side (ADP-toml-contract): dumps then loads gives the data back, a document round
    # trips through dumps, and a bad document raises the one error family.
    assert io.loads(io.dumps(data)) == data
    assert io.loads(io.dumps(io.document(SAMPLE))) == data
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


def test_the_error_is_a_decode_error_that_prints_and_carries_its_attributes():
    err = TomlError("bad")
    assert "TOMLDecodeError" in [
        c.__name__ for c in type(err).__mro__
    ]  # callers' except still works
    assert isinstance(err, ValueError) and str(err) == "bad"
    assert (err.msg, err.lineno, err.colno) == ("bad", 1, 1)
    assert err.args == ("bad", "", 0)
    with pytest.raises(TomlError, match="bad"):
        raise err
    with pytest.raises(TomlError) as caught:
        TomlLibIo().loads("a = ]")
    assert str(caught.value)  # printable: no AttributeError from a missing `pos`


def test_the_real_error_keeps_the_position_tomllib_found():
    with pytest.raises(TomlError) as caught:
        TomlLibIo().loads("a = 1\nb = ]\n")
    assert caught.value.lineno == 2 and caught.value.colno > 1


def test_the_fake_rejects_what_tomllib_rejects_in_its_subset():
    both = ("[a]\n[a]\n", "[a..b]\n", "[.a]\n")  # the real adapter rejects these too
    for bad in (*both, "x = null\n", "x = [null]\n", 'x = "\\/"\n'):  # not TOML
        with pytest.raises(TomlError):
            FakeTomlIo().loads(bad)
    with pytest.raises(TomlError):
        FakeTomlIo().loads("x = NaN\n")
    assert FakeTomlIo().loads("[a.b]\n[a]\n") == TomlLibIo().loads("[a.b]\n[a]\n")  # valid TOML
    with pytest.raises(TomlError):  # valid TOML, but outside the fake's subset: it says so
        FakeTomlIo().loads("x = {}\n")
    for bad in (*both, "x = null\n", 'x = "\\/"\n'):
        with pytest.raises(TomlError):
            TomlLibIo().loads(bad)


def test_the_error_survives_pickle_with_its_position():
    import pickle

    with pytest.raises(TomlError) as caught:
        TomlLibIo().loads("a = 1\nb = ]\n")
    back = pickle.loads(pickle.dumps(caught.value))
    assert (str(back), back.lineno, back.colno) == (str(caught.value), 2, caught.value.colno)
    assert (back.args, back.doc, back.pos) == (
        caught.value.args,
        caught.value.doc,
        caught.value.pos,
    )


def test_read_of_a_non_utf8_file_is_a_toml_error(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(TomlError):
        TomlLibIo().read(path)


def test_inline_comments_are_ignored_by_both_but_not_inside_strings():
    text = 'key = 1 # why\ns = "a # b"  # c\n'
    for io in (TomlLibIo(), FakeTomlIo()):
        assert io.loads(text) == {"key": 1, "s": "a # b"}


def test_a_document_edit_keeps_comments_and_layout():
    # tomlkit's promise, the write side exists to honour (ADP-toml-contract)
    text = '# top\nname = "x"  # why\n\n[a]\nk = 1\n'
    doc = TomlLibIo().document(text)
    assert TomlLibIo().dumps(doc) == text
    doc["a"]["k"] = 2
    assert TomlLibIo().dumps(doc) == text.replace("k = 1", "k = 2")


def test_a_bad_document_carries_tomlkit_position():
    with pytest.raises(TomlError) as caught:
        TomlLibIo().document("a = 1\nb = ]\n")
    assert caught.value.lineno == 2 and caught.value.colno > 1
