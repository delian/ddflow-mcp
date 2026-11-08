"""Review rounds converge (bug Bf797745b5a): a severity floor, not the round budget, is
what ends a re-review.

On a large-enough diff a reviewer kept raising a NEW MEDIUM finding every round (git
2.20 compat, path spellings, empty-string config) after its HIGH findings were fixed,
so two 15-25 min re-reviews of two reviewers each burned the `[review].max_rounds`
budget and the gate stayed failed with no round left. The convergence rule: the FIRST
round blocks on every finding, but a LATER round blocks only on findings at or above
`[review].converge_floor` (HIGH by default) -- once the material findings are fixed, a
reviewer raising a new MEDIUM/LOW nit against a file unchanged since the last review
does not re-open the gate. The below-floor findings are still recorded and triageable.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git

import ddflow.api.review as api
from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def _setup(repo: Path, tmp_path: Path, *, first: str, later: str) -> Path:
    """A command reviewer whose finding severity depends on the call count: round 1 says
    `first`, every later round says `later`. A counter file keeps it deterministic (with
    `hedge = 1` each round is exactly one call)."""
    counter = tmp_path / "counter"
    cli = tmp_path / "fake-reviewer"
    cli.write_text(
        "#!/bin/sh\n"
        "in=$(cat)\n"
        f'printf "%s" "$in" > "{tmp_path / "prompt.txt"}"\n'
        f'n=0\n[ -f "{counter}" ] && n=$(cat "{counter}")\n'
        "n=$((n+1))\n"
        f'printf "%s" "$n" > "{counter}"\n'
        f'if [ "$n" -eq 1 ]; then printf "FINDING {first} x.py:1\\nsome problem\\n\\n'
        'STATUS: FINDINGS 1\\n"; '
        f'else printf "FINDING {later} x.py:1\\na new nit (round $n)\\n\\nSTATUS: FINDINGS 1\\n"; fi\n'
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic", "rubber_duck"]\nhedge = 1\n'
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "x.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")
    return counter


def _review(repo: Path, **kw):
    return api.review(repo, gate="critic", item="T1", branch="feat", **kw)


def _fix(repo: Path) -> None:
    (repo / "x.py").write_text("x = 2  # the defect is fixed\n")
    _git(repo, "add", "x.py")
    _git(repo, "commit", "-qm", "fix the finding")


def _settle_first_as_fixed(repo: Path) -> None:
    run_cli(
        repo, "review", "triage", "T1", "--gate", "critic", "--finding", "1",
        "--confirmed", "--probe", "x.py was fixed in the follow-up commit",
    )  # fmt: skip


def _ev(repo: Path) -> dict:
    return fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["critic"].evidence


def test_a_later_rounds_new_medium_finding_does_not_block_the_pass(repo, tmp_path):
    """The regression: the HIGH finding is fixed, and the re-review's NEW MEDIUM nit is
    recorded but does not keep the gate failed -- the round converges. A confirmed finding
    needs a delta round that did not raise it again before a full round may converge
    (D-failed-review-gate, bug B-push-failed-review-delta), so the fix gets one first."""
    _setup(repo, tmp_path, first="HIGH", later="MEDIUM")

    first = _review(repo)
    assert first.data["outcome"] == "failed" and first.exit == 0

    _settle_first_as_fixed(repo)
    _fix(repo)
    assert _review(repo).data["outcome"] == "failed"
    later = _review(repo, full=True)

    assert later.data["outcome"] == "passed", later.data
    ev = _ev(repo)
    assert ev["converged"] == {
        "findings_below_floor": 1,
        "floor": "HIGH",
    }, ev
    # The new MEDIUM nit is recorded (with the earlier HIGH kept beside it), just not blocking.
    assert "MEDIUM" in [f["severity"] for f in ev["chunk_findings"]], ev["chunk_findings"]


def test_a_later_rounds_new_high_finding_still_blocks(repo, tmp_path):
    """The floor is not a free pass: a NEW HIGH finding in a later round still fails."""
    _setup(repo, tmp_path, first="HIGH", later="HIGH")

    assert _review(repo).data["outcome"] == "failed"
    _settle_first_as_fixed(repo)
    _fix(repo)
    assert _review(repo, full=True).data["outcome"] == "failed"


def test_the_first_round_blocks_on_a_medium_finding(repo, tmp_path):
    """The first round has nothing to converge from: a first-round MEDIUM finding blocks
    (a later-round-only floor must not hide it)."""
    _setup(repo, tmp_path, first="MEDIUM", later="MEDIUM")
    out = _review(repo)
    assert out.data["outcome"] == "failed", out.data
    assert "converged" not in _ev(repo), _ev(repo)


def test_an_unknown_severity_blocks_a_converged_round():
    """Fail closed: a severity the floor cannot rank is never a free pass."""
    from ddflow.services.review import Finding, blocking_findings

    odd = Finding(severity="CRITICAL", location="x.py:1", title="t", detail="d")
    low = Finding(severity="LOW", location="x.py:1", title="t", detail="d")
    assert blocking_findings([odd, low], converged=True, floor="HIGH") == [odd]


def test_the_floor_paragraph_is_only_sent_where_the_floor_applies():
    """A chunk re-run or fix round blocks at any severity, so its prompt must not tell the
    reviewer to hold back below-floor findings; a full or delta round does."""
    assert api._floor_lines("") == []
    assert "below HIGH severity" in "".join(api._floor_lines("HIGH"))


def test_a_converged_round_weighs_every_reviewers_findings(monkeypatch):
    """Two reviewers: the first (status-minimal) has only a LOW nit, the second a HIGH one.
    The HIGH must still block -- the floor never hides another reviewer's finding."""
    from types import SimpleNamespace as NS

    from ddflow.config import Config
    from ddflow.services import review as RV

    monkeypatch.setattr(
        api.records, "_last_review", lambda it, gate: {"chunk_findings": [{"severity": "HIGH"}]}
    )
    low = NS(status=RV.REVIEWED, findings=[RV.Finding(severity="LOW", title="nit")])
    high = NS(status=RV.REVIEWED, findings=[RV.Finding(severity="HIGH", title="bad")])
    cfg = Config.load(None, env={})
    outcome, _ = api._gate_outcome(
        low, [low, high], object(), "T1", "critic", True, cfg, None, print
    )
    assert outcome == "failed"
    outcome, ev = api._gate_outcome(
        low, [low, low], object(), "T1", "critic", True, cfg, None, print
    )
    assert outcome == "passed" and ev["converged"]["findings_below_floor"] == 2


def test_a_partial_reviewers_finding_blocks_beside_a_clean_reviewer():
    """A reviewer that lost a chunk was still heard: its HIGH finding of the chunks it did
    finish must not vanish because a peer came back clean (bug B-main-red-review-records)."""
    from types import SimpleNamespace as NS

    from ddflow.config import Config
    from ddflow.services import review as RV

    clean = NS(status=RV.REVIEWED, findings=[])
    partial = NS(status=RV.PARTIAL, findings=[RV.Finding(severity="HIGH", title="bad")])
    cfg = Config.load(None, env={})
    outcome, _ = api._gate_outcome(
        clean, [clean, partial], None, "", "critic", False, cfg, None, print
    )
    assert outcome == "failed"


def test_the_floor_is_named_for_a_full_or_delta_round_but_not_a_fix_round():
    """Bug B-review-delta-rounds-do-not-converge: a delta re-review converges too, so its
    prompt names the floor; only a fix round and a chunk re-run keep blocking at any
    severity."""
    from ddflow.config import Config

    cfg = Config.load(None, env={})
    assert api._floor_for(cfg, "full", False) == "HIGH"
    assert api._floor_for(cfg, "delta", False) == "HIGH"
    assert api._floor_for(cfg, "full", True) == ""
    assert api._converges("delta", False) is True
    assert api._converges("delta", True) is False


def test_a_non_converged_round_weighs_every_reviewers_findings():
    from types import SimpleNamespace as NS

    from ddflow.config import Config
    from ddflow.services import review as RV

    clean = NS(status=RV.REVIEWED, findings=[])
    high = NS(status=RV.REVIEWED, findings=[RV.Finding(severity="HIGH", title="bad")])
    cfg = Config.load(None, env={})
    outcome, _ = api._gate_outcome(
        clean, [clean, high], None, "T1", "critic", False, cfg, None, print
    )
    assert outcome == "failed"


def test_a_round_with_no_earlier_findings_does_not_converge(monkeypatch):
    """An earlier unavailable round spends a round but leaves nothing to converge from:
    the first substantive review blocks on a MEDIUM finding."""
    from types import SimpleNamespace as NS

    from ddflow.config import Config
    from ddflow.services import review as RV

    monkeypatch.setattr(api.records, "_last_review", lambda it, gate: {})
    med = NS(status=RV.REVIEWED, findings=[RV.Finding(severity="MEDIUM", title="m")])
    cfg = Config.load(None, env={})
    outcome, _ = api._gate_outcome(med, [med], object(), "T1", "critic", True, cfg, None, print)
    assert outcome == "failed"


def test_a_repeated_untriaged_below_floor_finding_still_blocks(monkeypatch):
    """Bug B-floor-passes-repeated-untriaged-nit: an earlier below-floor finding the author
    neither fixed nor answered, raised AGAIN by a later full re-review, keeps blocking --
    the floor filters only NEW below-floor findings."""
    from types import SimpleNamespace as NS

    from ddflow.config import Config
    from ddflow.services import review as RV

    prior = {
        "chunk_findings": [
            {
                "severity": "MEDIUM",
                "digest": "abc123",
                "title": "m",
                "location": "x.py:1",
                "detail": "d",
            }
        ]
    }
    monkeypatch.setattr(api.records, "_last_review", lambda it, gate: prior)
    repeated = NS(
        status=RV.REVIEWED, findings=[RV.Finding(severity="MEDIUM", digest="abc123", title="m")]
    )
    cfg = Config.load(None, env={})
    outcome, _ = api._gate_outcome(
        repeated, [repeated], object(), "T1", "critic", True, cfg, None, print
    )
    assert outcome == "failed"

    # A NEW below-floor finding of the same severity is still filtered (the floor stands).
    fresh = NS(
        status=RV.REVIEWED, findings=[RV.Finding(severity="MEDIUM", digest="xyz", title="m")]
    )
    outcome, ev = api._gate_outcome(
        fresh, [fresh], object(), "T1", "critic", True, cfg, None, print
    )
    assert outcome == "passed" and ev["converged"]["findings_below_floor"] == 1


def test_a_delta_round_converges_on_a_new_below_floor_finding(monkeypatch):
    """Bug B-review-delta-rounds-do-not-converge: a DELTA round's new below-floor finding is
    recorded but does not keep the gate failed. Before the fix `_converges` returned False
    for a delta, so every new nit re-opened the gate and it never converged."""
    from types import SimpleNamespace as NS

    from ddflow.config import Config
    from ddflow.services import review as RV

    prior = {
        "review_kind": "delta",
        "rounds": 1,
        "chunk_findings": [
            {
                "severity": "HIGH",
                "digest": "abc123",
                "title": "h",
                "location": "x.py:1",
                "detail": "d",
            }
        ],
    }
    monkeypatch.setattr(api.records, "_last_review", lambda it, gate: prior)
    nit = NS(
        status=RV.REVIEWED, findings=[RV.Finding(severity="LOW", title="new nit", digest="xyz")]
    )
    cfg = Config.load(None, env={})
    outcome, ev = api._gate_outcome(
        nit, [nit], object(), "T1", "critic", api._converges("delta", False), cfg, None, print
    )
    assert outcome == "passed", outcome
    assert ev["converged"] == {"findings_below_floor": 1, "floor": "HIGH"}, ev


def test_a_delta_round_new_high_finding_still_blocks(monkeypatch):
    """The floor is not a free pass for a delta: a new HIGH finding still fails it."""
    from types import SimpleNamespace as NS

    from ddflow.config import Config
    from ddflow.services import review as RV

    prior = {
        "review_kind": "delta",
        "rounds": 1,
        "chunk_findings": [
            {
                "severity": "HIGH",
                "digest": "abc123",
                "title": "h",
                "location": "x.py:1",
                "detail": "d",
            }
        ],
    }
    monkeypatch.setattr(api.records, "_last_review", lambda it, gate: prior)
    high = NS(status=RV.REVIEWED, findings=[RV.Finding(severity="HIGH", title="bad", digest="qqq")])
    cfg = Config.load(None, env={})
    outcome, _ = api._gate_outcome(
        high, [high], object(), "T1", "critic", api._converges("delta", False), cfg, None, print
    )
    assert outcome == "failed"


def test_a_delta_own_new_finding_does_not_hold_the_pass():
    """Bug B-review-delta-rounds-do-not-converge: a fresh finding the delta itself raised is
    not an EARLIER finding, so `_untriaged` does not count it against the pass -- its blocking
    weight was already judged. A byte-identical re-raise (not ``new``) still holds."""
    from ddflow.api.review.findings import _untriaged

    kept = [
        {"digest": "a", "severity": "HIGH"},
        {"digest": "b", "severity": "LOW"},
    ]
    verdicts = {}  # both untriaged
    # the delta raised "b" itself (new); only "a" (earlier) holds
    assert _untriaged(kept, gone=set(), carried={}, verdicts=verdicts, new={"b"}) == 1
    # no new set: both hold (the pre-fix behaviour)
    assert _untriaged(kept, gone=set(), carried={}, verdicts=verdicts) == 2


def test_a_refuted_prior_finding_is_not_still_standing(monkeypatch):
    """`_still_standing` drops a REFUTED prior finding (settled, no longer stands: re-raising
    it below the floor is filtered), but keeps a CONFIRMED one (still a real defect: blocks)."""
    from types import SimpleNamespace as NS

    from ddflow.config import Config
    from ddflow.services import review as RV

    prior = {
        "chunk_findings": [
            {
                "severity": "MEDIUM",
                "digest": "abc123",
                "title": "m",
                "location": "x.py:1",
                "detail": "d",
            }
        ]
    }
    monkeypatch.setattr(api.records, "_last_review", lambda it, gate: prior)
    cfg = Config.load(None, env={})
    repeated = NS(
        status=RV.REVIEWED, findings=[RV.Finding(severity="MEDIUM", digest="abc123", title="m")]
    )

    refuted = NS(triage={"critic": {"abc123": {"verdict": "refuted"}}})
    outcome, _ = api._gate_outcome(
        repeated, [repeated], refuted, "T1", "critic", True, cfg, None, print
    )
    assert outcome == "passed"

    confirmed = NS(triage={"critic": {"abc123": {"verdict": "confirmed"}}})
    outcome, _ = api._gate_outcome(
        repeated, [repeated], confirmed, "T1", "critic", True, cfg, None, print
    )
    assert outcome == "failed"
