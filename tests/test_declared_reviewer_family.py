"""Reviewer independence uses the family `ddflow review` recorded (bug B6ed8b9edb8).

A LAN vLLM server may serve a model under another model's name: this project's critic is
Qwen3.8-27B served as `google/gemma-4-31B-it`, and its reviewer entry declares
`family = "alibaba"` for exactly that reason. `ddflow review` records the declared family
in the gate evidence, but the independence check guessed the family from the model name
and reported "critic was google". The declared family must win -- but only where the
operator declared it: an agent's `gate record` cannot write a family, so a manual record
is still judged by its model name.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.core.model import fold
from ddflow.services import gates as G

SERVED = "google/gemma-4-31B-it"


@pytest.fixture
def gd(repo, cfg):
    return G.load_gates(repo, cfg)


def _item(log):
    log.append("phase.added", "P1", {})
    log.append("task.added", "T1", {"parent": "P1"})


def _review(family: str) -> dict:
    """The evidence shape `ddflow review` records (api/review.py)."""
    return {"model": SERVED, "reviewer": "lan-qwen3.8-27b", "family": family, "status": "REVIEWED"}


def test_a_review_recorded_family_wins_over_the_served_name(log, cfg, gd):
    _item(log)
    G.record(log, cfg, "T1", "critic", "passed", evidence=_review("alibaba"), gates=gd)
    ok, why = G.reviewer_independence(fold(log.read_all()), cfg, "T1", "claude-opus-5")
    assert ok and "alibaba" in why and "google" not in why, why


def test_a_declared_family_equal_to_the_authors_is_not_independent(log, cfg, gd):
    """The dangerous direction: a served name from another family must not rescue a
    reviewer the operator declared to be the author's own family."""
    _item(log)
    G.record(log, cfg, "T1", "critic", "passed", evidence=_review("anthropic"), gates=gd)
    ok, why = G.reviewer_independence(fold(log.read_all()), cfg, "T1", "claude-opus-5")
    assert not ok and "same as the author" in why, why


def test_a_manual_record_is_still_judged_by_its_model_name(log, cfg, gd):
    """No `reviewer` key: not written by `ddflow review`, so no declared family to trust."""
    _item(log)
    G.record(
        log,
        cfg,
        "T1",
        "critic",
        "passed",
        evidence={"model": SERVED, "family": "alibaba"},
        gates=gd,
    )
    ok, why = G.reviewer_independence(fold(log.read_all()), cfg, "T1", "claude-opus-5")
    assert ok and "google" in why, why


@pytest.mark.parametrize("family", ["", "unknown", "UNKNOWN"])
def test_an_undeclared_family_falls_back_to_the_name(log, cfg, gd, family):
    _item(log)
    G.record(log, cfg, "T1", "critic", "passed", evidence=_review(family), gates=gd)
    ok, why = G.reviewer_independence(fold(log.read_all()), cfg, "T1", "claude-opus-5")
    assert ok and "google" in why, why
