"""DX-recover-brief-slice: the brief reserves a slice for recoverable work that
`session.brief_max_tokens` never cuts, and each entry says what was interrupted and
that ddflow has not resumed it (folded B-res-brief-recovery-link; D-orphan-recovery).

The brief used to budget the recovery section like any other (B1472311a63), so a tiny
budget cut the very entries an agent must see first. The slice is now reserved: the
squeeze takes from the budgeted sections, and every listed entry names the exact
command (`ddflow claim …`, `ddflow complete …`) and what the lost session still owed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import lifecycle as LC
from ddflow.infra.log import EventLog

RESUME = "## ⚠ RESUME: recoverable work found"


def _seed(repo: Path, ids: list[str]) -> EventLog:
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "Phase one"})
    for t in ids:
        log.append("task.added", t, {"parent": "P1", "title": t, "globs": [f"src/{t}.py"]})
    return log


def _blow(log: EventLog, n: int = 14) -> None:
    """More lessons and a heavy decision than any brief needs: with a small budget the
    sections must be trimmed, which is what the recovery slice has to survive."""
    for i in range(n):
        log.append(
            "lesson.recorded",
            f"L{i}",
            {"title": f"lesson {i}", "rule": "rule " + ("x" * 400)},
        )


def test_the_reserved_slice_survives_a_budget_that_trims_the_rest(repo):
    log = _seed(repo, ["T1", "T9"])
    log.append("item.started", "T1", {})
    _blow(log)
    run_cli(repo, "config", "session.brief_max_tokens", "400")
    out = LC.brief(repo, item="T9", check_recovery=True, agent="next")
    assert out.exit == 0, out.reason
    text = out.data["text"]
    # The budget DID squeeze the brief: somewhere a section was cut...
    assert "cut to fit session.brief_max_tokens" in text, text[:2000]
    # ...and the recovery block is complete: the alarm entry is not the cut one.
    assert RESUME in text, text[:2000]
    assert "**T1** (stale_running" in text, text
    assert "`ddflow claim T1` to resume" in text, text
    # The claim command the status line refers to is PRESENT, above it (duck #6).
    assert text.index("`ddflow claim T1` to resume") < text.index("takes it over"), text
    assert text.index(RESUME) < text.index("cut to fit session.brief_max_tokens")
    # The ENTRY survives the cut the same way the heading does: nothing may cut in
    # between them (duck #7).
    assert text.index("**T1** (stale_running") < text.index("cut to fit session.brief_max_tokens")


def test_each_entry_says_what_was_interrupted_and_that_ddflow_has_not_resumed(repo):
    log = _seed(repo, ["T1", "T9"])
    log.append("item.started", "T1", {})
    log.append(
        "gate.passed",
        "T1",
        {"gate": "research", "by": "m"},
    )
    out = LC.brief(repo, item="T9", check_recovery=True, agent="next")
    assert out.exit == 0, out.reason
    text = out.data["text"]
    assert RESUME in text, text[:3000]
    assert "NOT resumed: nobody holds it now — the claim command above takes it over" in text
    assert "gates done: research" in text, text
    assert "next gate: rules" in text, text


def test_the_last_prompt_of_the_lost_holder_is_in_the_slice(repo):
    """The paragraph the crash lost is handed to the fresh agent with the exact command."""
    log = _seed(repo, ["T1", "T9"])
    crashed = EventLog(repo, "crashed")
    crashed.append("session.started", "S1", {"model": "mm"})
    crashed.append(
        "session.prompt",
        "S1",
        {"text": "finish the graph parser before lunch", "item": "T1"},
    )
    crashed.append(
        "session.prompt",
        "S1",
        {"text": "launder the decoy socks instead", "item": "T9"},
    )
    claim = LC.claim(repo, "T1", agent="crashed")
    assert claim.exit == 0, claim.reason
    wt = Path(claim.data["worktree"])
    (wt / "parser.py").write_text("half done\n")
    (wt / ".gitkeep").write_text("")
    log.append(
        "lease.acquired",
        "T1",
        {
            "holder": "crashed",
            "at": 1900.0,  # long lapsed: the lease is expired, the tree holds work
            "ttl_s": 60,
            "worktree": str(wt),
            "branch": claim.data["branch"],
        },
    )
    out = LC.brief(repo, item="T9", check_recovery=True, agent="next")
    assert out.exit == 0, out.reason
    text = out.data["text"]
    assert "**T1** (expired_lease, was crashed)" in text, text
    fact = [l for l in text.splitlines() if "last prompt:" in l]
    assert fact and "graph parser before lunch" in fact[0], text
    # A prompt with another item's id is that item's context, never borrowed for T1
    # (critic: local to the T1 fact line, not a global absence claim).
    assert "decoy" not in fact[0], text
    assert "NOT resumed: ddflow applies only safe actions" in text, text
    # The exact command for THIS entry's band is named (duck #9).
    assert "`ddflow release T1 --note salvaged`" in text, text


def test_many_entries_keep_the_slice_bounded_and_its_cap_named(repo):
    """The slice is reserved from the budget, not from its own per-band cap."""
    log = _seed(repo, ["T1", *[f"W{i}" for i in range(7)], "T9"])
    log.append("item.started", "T1", {})
    for i in range(7):
        claim = LC.claim(repo, f"W{i}", agent="crashed")
        wt = Path(claim.data["worktree"])
        (wt / "work.py").write_text("x = 1\n")
        log.append(
            "lease.acquired",
            f"W{i}",
            {
                "holder": "crashed",
                "at": 1900.0,
                "ttl_s": 60,
                "worktree": str(wt),
                "branch": claim.data["branch"],
            },
        )
    out = LC.brief(repo, item="T9", check_recovery=True, agent="next")
    assert out.exit == 0, out.reason
    text = out.data["text"]
    # The RESUME slice is the region between its heading and the pack's own H1: the cap
    # assertions BEGIN and END there (critic: W* titles can appear outside the slice).
    end = text.index("# ddflow brief")
    region = text[text.index(RESUME) : end]
    assert "**W4**" in region and "2 more holding work" in region, region
    # The cap is APPLIED, not just reported: exactly W0..W4 shown, the rest not (duck).
    for shown in (f"**W{i}**" for i in range(5)):
        assert shown in region, region
    assert "**W5**" not in region and "**W6**" not in region, region
    # The RUNNING item nobody holds is still on the list beside seven holding trees.
    assert "**T1** (stale_running" in text, text
