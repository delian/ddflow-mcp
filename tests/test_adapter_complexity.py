"""The `Complexity` port: the default adapter's behaviour and the fake's (CONFORMANCE)."""

from __future__ import annotations

from ddflow.adapters import complexity as N
from ddflow.adapters.complexity import Complexity
from ddflow.adapters.fakes import FAKES
from ddflow.adapters.fakes.complexity import FakeComplexity


def complexity_conformance(c: Complexity) -> None:
    assert c.complexities(None) == {}
    assert isinstance(c.complexities(b"def f():\n    pass\n"), dict)


CONFORMANCE = {
    "Complexity": {"real": lambda monkeypatch: N.RadonComplexity(), "cases": complexity_conformance}
}


def test_the_adapter_and_the_fake_satisfy_the_port():
    assert isinstance(N.RadonComplexity(), Complexity) and isinstance(FakeComplexity(), Complexity)
    assert FAKES["Complexity"] is FakeComplexity


def test_the_fake_returns_the_table_it_was_built_with():
    assert FakeComplexity({"f": (2, 18)}).complexities(b"x") == {"f": (2, 18)}
