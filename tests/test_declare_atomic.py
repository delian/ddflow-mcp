"""A refused `declare()` must leave no registry entry behind (it polluted later tests)."""

from dataclasses import dataclass

import pytest

from ddflow.config_sections import _docs
from ddflow.config_sections._docs import knob

SINCE = "0.2.0"


def _refused_then_clean(monkeypatch):
    for table in ("KNOB_DOCS", "DECLARED", "DECLARED_IN", "RENAMED", "SECTION_CLASSES"):
        monkeypatch.setattr(_docs, table, dict(getattr(_docs, table)))
    monkeypatch.setitem(_docs.RENAMED, "zy.old", ("zy.new", SINCE, "1.0"))

    @dataclass
    class Late:
        old: int = knob(1, doc="d")

    with pytest.raises(ValueError, match="also a live key"):
        _docs.declare("zy")(Late)
    return Late


def test_a_refused_declare_leaves_no_section_behind(monkeypatch):
    _refused_then_clean(monkeypatch)
    assert "zy" not in _docs.SECTION_CLASSES
    assert "zy.old" not in _docs.KNOB_DOCS and "zy.old" not in _docs.DECLARED


def test_the_section_can_be_declared_again_after_a_refusal(monkeypatch):
    late = _refused_then_clean(monkeypatch)
    monkeypatch.delitem(_docs.RENAMED, "zy.old")
    _docs.declare("zy")(late)
    assert "zy.old" in _docs.DECLARED
