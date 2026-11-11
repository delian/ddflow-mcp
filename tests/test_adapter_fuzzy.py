"""The `Fuzzy` port: the default adapter's behaviour and the fake's (CONFORMANCE)."""

from __future__ import annotations

from ddflow.adapters import fuzzy as N
from ddflow.adapters.fakes import FAKES
from ddflow.adapters.fakes.fuzzy import FakeFuzzy
from ddflow.adapters.fuzzy import Fuzzy


def fuzzy_conformance(f: Fuzzy) -> None:
    assert isinstance(f.available(), bool)
    assert f.ratio("same", "same") == 1.0
    assert f.ratio("DNS", "dns") == 1.0 and f.ratio("dns", "DNS") == 1.0  # case-folded
    assert f.ratio("a", "b") == 0.0
    assert 0.0 <= f.ratio("apple", "banana") <= 1.0


CONFORMANCE = {"Fuzzy": {"real": lambda monkeypatch: N.DefaultFuzzy(), "cases": fuzzy_conformance}}


def test_the_adapter_and_the_fake_satisfy_the_port():
    assert isinstance(N.DefaultFuzzy(), Fuzzy) and isinstance(FakeFuzzy(), Fuzzy)
    assert FAKES["Fuzzy"] is FakeFuzzy


def test_the_module_level_functions_delegate_to_the_default_adapter():
    assert N.ratio("same", "same") == 1.0 and N.ratio("a", "b") == 0.0
    assert isinstance(N.available(), bool)
