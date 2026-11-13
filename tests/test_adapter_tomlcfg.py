"""The `Toml` (config overlay) port: method-shape cases against `FakeToml` and the real
module (`ddflow.infra.tomlcfg`).

`CONFORMANCE` is read by the generic contract test (tests/test_ports_contract.py).
"""

from __future__ import annotations

from ddflow.adapters.fakes.tomlcfg import FakeToml
from ddflow.infra import tomlcfg as real_toml


def toml_cases(io) -> None:
    for name in ("overlay_table", "upsert", "remove", "move_keys"):
        assert callable(getattr(io, name))


CONFORMANCE = {
    "Toml": {
        "real": lambda monkeypatch: real_toml,
        "cases": toml_cases,
        "fake": lambda cls: cls(),
    }
}


def test_the_fake_records_text_edits():
    io = FakeToml()
    io.upsert("a = 1\n", "b", "2")
    io.remove("a = 1\n", "b")
    assert io.calls == [("upsert", ("b", "2")), ("remove", ("b",))]
