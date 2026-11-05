"""The `Env` port: the default adapter's behaviour and the fake's (CONFORMANCE)."""

from __future__ import annotations

from ddflow.adapters import env as E
from ddflow.adapters.env import Env
from ddflow.adapters.fakes import FAKES
from ddflow.adapters.fakes.env import FakeEnv

VAR = "DDFLOW_TEST_ENV_PORT"


def env_conformance(e: Env) -> None:
    """The factories set VAR=on and VAR_EMPTY="" and leave VAR_MISSING unset."""
    assert e.get(VAR) == "on"
    assert e.get(VAR + "_MISSING") is None
    assert e.get(VAR + "_EMPTY") == ""  # set but empty is not unset
    assert e.get(VAR + "_MISSING", "dflt") == "dflt"
    snap = e.snapshot()
    assert snap[VAR] == "on"
    snap[VAR] = "changed"  # a copy: changing it changes nothing
    assert e.get(VAR) == "on"
    assert e.snapshot()[VAR] == "on"  # and a fresh one each time


def _real(monkeypatch) -> E.SystemEnv:
    monkeypatch.setenv(VAR, "on")
    monkeypatch.setenv(VAR + "_EMPTY", "")
    monkeypatch.delenv(VAR + "_MISSING", raising=False)
    return E.SystemEnv()


CONFORMANCE = {
    "Env": {
        "real": _real,
        "fake": lambda cls: cls({VAR: "on", VAR + "_EMPTY": ""}),
        "cases": env_conformance,
    }
}


def test_the_adapter_and_the_fake_satisfy_the_port():
    assert isinstance(E.SystemEnv(), Env) and isinstance(FakeEnv(), Env)
    assert FAKES["Env"] is FakeEnv


def test_the_adapter_reads_the_live_environment_each_call(monkeypatch):
    monkeypatch.setenv(VAR, "a")
    assert E.DEFAULT.get(VAR) == "a"
    monkeypatch.setenv(VAR, "b")
    assert E.DEFAULT.get(VAR) == "b"


def test_the_fake_never_sees_the_machine_environment(monkeypatch):
    monkeypatch.setenv(VAR, "host")
    f = FakeEnv()
    assert f.get(VAR) is None and f.snapshot() == {}
    f.set(VAR, "x")
    assert f.get(VAR) == "x"
    f.unset(VAR)
    assert f.get(VAR, "d") == "d"
