"""`ddflow review <id> <gate>` takes the gate positionally like every other gate command
(DX-review-gate-positional); it used to fail with 'review takes one item id'."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli


def test_the_gate_may_follow_the_item_positionally(repo):
    assert run_cli(repo, "init")[0] == 0
    _code, out, err = run_cli(repo, "review", "NOPE", "rubber_duck")
    assert "takes one item id" not in out + err, out + err


def test_the_positional_gate_reaches_the_review_as_its_gate(repo, monkeypatch):
    from types import SimpleNamespace as NS

    from ddflow.surfaces.commands import review as R

    seen = {}

    def fake(_repo, **kw):
        seen.update(kw)
        from ddflow.core import outcome as O

        return O.ok("review", reviewer="x")

    monkeypatch.setattr(R.A, "review", fake)
    a = NS(id=["T1", "rubber_duck"], gate="", intent="", context="", base="", commit="",
           branch="", chunk=None, delta=False, force=False, reason="", finding=None,
           refuted=None, confirmed=None, probe=None)  # fmt: skip
    c = NS(repo=repo, called_from=None, json=False, requested_agent="")
    assert R.cmd_review(a, c) == 0
    assert (seen["item"], seen["gate"]) == ("T1", "rubber_duck")


def test_two_different_gates_are_refused(repo, capsys):
    from types import SimpleNamespace as NS

    from ddflow.surfaces.commands import review as R

    a = NS(
        id=["T1", "critic"],
        gate="rubber_duck",
        finding=None,
        refuted=None,
        confirmed=None,
        probe=None,
    )
    assert R.cmd_review(a, NS(repo=repo, json=False)) == 1
    assert "twice" in capsys.readouterr().err
