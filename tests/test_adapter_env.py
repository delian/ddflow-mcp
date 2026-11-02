"""The `Env` port: the default adapter's behaviour and the fake's (CONFORMANCE)."""

from __future__ import annotations

from ddflow.adapters import env as E
from ddflow.adapters.env import Env
from ddflow.adapters.fakes import FAKES
from ddflow.adapters.fakes.env import FakeEnv

VAR = "DDFLOW_TEST_ENV_PORT"


def env_conformance(e: Env) -> None:
    """The factories set VAR=on and leave VAR_MISSING unset."""
    assert e.get(VAR) == "on"
    assert e.get(VAR + "_MISSING") == ""
    assert e.get(VAR + "_MISSING", "dflt") == "dflt"
    snap = e.snapshot()
    assert snap[VAR] == "on"
    snap[VAR] = "changed"  # a copy: changing it changes nothing
    assert e.get(VAR) == "on"


def _real(monkeypatch) -> E.SystemEnv:
    monkeypatch.setenv(VAR, "on")
    monkeypatch.delenv(VAR + "_MISSING", raising=False)
    return E.SystemEnv()


CONFORMANCE = {
    "Env": {"real": _real, "fake": lambda cls: cls({VAR: "on"}), "cases": env_conformance}
}


def test_the_adapter_and_the_fake_satisfy_the_port():
    assert isinstance(E.SystemEnv(), Env) and isinstance(FakeEnv(), Env)
    assert FAKES["Env"] is FakeEnv
    assert E.default_env() is E.DEFAULT


def test_the_adapter_reads_the_live_environment_each_call(monkeypatch):
    monkeypatch.setenv(VAR, "a")
    assert E.DEFAULT.get(VAR) == "a"
    monkeypatch.setenv(VAR, "b")
    assert E.DEFAULT.get(VAR) == "b"


def test_the_fake_never_sees_the_machine_environment(monkeypatch):
    monkeypatch.setenv(VAR, "host")
    f = FakeEnv()
    assert f.get(VAR) == "" and f.snapshot() == {}
    f.set(VAR, "x")
    assert f.get(VAR) == "x"
    f.unset(VAR)
    assert f.get(VAR, "d") == "d"
