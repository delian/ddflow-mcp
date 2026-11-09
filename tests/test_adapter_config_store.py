"""The `ConfigStore` port: one set of cases against `LocalConfigStore` and the shipped
`FakeConfigStore`, plus the real adapter's own promises (layers, sources, explain).

`CONFORMANCE` is read by the generic contract test (tests/test_ports_contract.py).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from ddflow.adapters.config_store import ConfigStore, LocalConfigStore
from ddflow.adapters.fakes.config_store import FakeConfigStore


def _resolved(io, root, *, env=None):
    """`resolve` both adapters return a value with the same reads."""
    r = io.resolve(root, env=env)
    return r.as_dict(), r.sources, r.explain()


def config_cases(io) -> None:
    assert isinstance(io, ConfigStore)
    root = Path(tempfile.mkdtemp())
    (root / ".ddflow").mkdir()
    (root / ".ddflow" / "config.toml").write_text("[lease]\nttl_s = 900\n", "utf-8")
    data, sources, explain = _resolved(io, root, env={})
    # the packaged defaults land; the committed override beats them and records its source
    assert sources.get("lease.ttl_s", "default") == "file"
    assert data["lease"]["ttl_s"] == 900
    # the explain log names every knob with a source and a doc
    by_key = {row[0]: row for row in explain}
    assert by_key["lease.ttl_s"][2] == "file" and isinstance(by_key["lease.ttl_s"][3], str)
    # validation raises on the first thing wrong, both adapters
    with pytest.raises(ValueError):
        io.validate({"lease": {"ttl_s": "not a number"}})


CONFORMANCE = {
    "ConfigStore": {
        "real": lambda monkeypatch: LocalConfigStore(),
        "cases": config_cases,
        "fake": lambda cls: cls({"lease.ttl_s": "900"}),
    }
}


def test_the_default_adapter_passes_the_config_cases():
    config_cases(LocalConfigStore())


def test_the_fake_passes_the_config_cases():
    config_cases(FakeConfigStore({"lease.ttl_s": "900"}))


def test_resolve_layers_over_env_in_order(tmp_path, monkeypatch):
    (tmp_path / ".ddflow").mkdir()
    (tmp_path / ".ddflow" / "config.toml").write_text("[lease]\nttl_s = 900\n", "utf-8")
    monkeypatch.setenv("DDFLOW_LEASE_TTL_S", "5")
    data, sources, _ = _resolved(LocalConfigStore(), tmp_path)
    assert sources["lease.ttl_s"] == "env" and data["lease"]["ttl_s"] == 5


def test_the_fake_keeps_an_empty_string_and_a_bool_typing(tmp_path):
    fake = FakeConfigStore({"ui.theme": "", "core.enabled": "true"})
    data, _, _ = _resolved(fake, tmp_path)
    assert data["ui"]["theme"] == "" and data["core"]["enabled"] is True
    fake.set(tmp_path, "core.enabled", False)
    assert fake.resolve(tmp_path).as_dict()["core"]["enabled"] is False
