"""The isolation guard must recognise syrupy's --snapshot-update session."""

from types import SimpleNamespace

from tests import _isolation


def _session(opts: dict):
    config = SimpleNamespace(getoption=lambda name, default=None: opts.get(name, default))
    return SimpleNamespace(config=config)


def test_guard_sees_syrupy_update_session(monkeypatch):
    monkeypatch.delenv("DDFLOW_UPDATE_GOLDEN", raising=False)
    assert _isolation._updating(_session({"update_snapshots": True})) is True


def test_guard_sees_plain_session(monkeypatch):
    monkeypatch.delenv("DDFLOW_UPDATE_GOLDEN", raising=False)
    assert _isolation._updating(_session({"update_snapshots": False})) is False
