"""`ddflow vigil`: one compact health snapshot, one call, one fold.

Regression test for bug B3a86c5cfb9: the coordinator reconstructed its hourly snapshot
via 5+ shell pipelines (bug count+severity, stale pins, active lanes, interrupted,
pending approvals, reviewer reachability). `vigil` returns that whole block in one pass,
reusing the existing verbs rather than reimplementing them -- so this test fails against
the unfixed code, which has no `vigil` at all.
"""

from __future__ import annotations

import pytest
from conftest import run_cli

from ddflow.api.reporting import vigil as VV


def _seed(repo):
    """A lane with an item, and open bugs of two severities plus one without."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "X", "--title", "X fixture", "--globs", "f.py")
    assert run_cli(repo, "claim", "X", "--no-worktree", agent="holder")[0] == 0
    for sev, summary in (("critical", "boom crit"), ("high", "boom high"), ("", "boom none")):
        args = ["bug", "found", "--summary", summary, "--no-task"]
        if sev:
            args += ["--severity", sev]
        code, out, err = run_cli(repo, *args)
        assert code == 0, (out, err)


def _quiet_pins_and_reviewers(monkeypatch, reviewers=(("gemini", "up"),)):
    """Deterministic, offline: the pins check runs the snapshot suite and the reviewer
    check probes endpoints; both are what this test is NOT about, so they are stubbed."""
    monkeypatch.setattr(VV, "_stale_pins", lambda repo, cfg: [])
    rows = [{"name": n, "reachability": r, "reason": ""} for n, r in reviewers]
    monkeypatch.setattr(VV, "_reviewer_rows", lambda repo, cfg, st, now: rows)


def test_vigil_reports_severities_lanes_and_a_reviewer_line(repo, monkeypatch):
    _seed(repo)
    _quiet_pins_and_reviewers(monkeypatch)

    out = VV.vigil(repo)

    assert out.exit == 0, out.reason
    bugs = out.data["bugs"]
    assert bugs["open_by_severity"]["critical"] == 1
    assert bugs["open_by_severity"]["high"] == 1
    assert bugs["open_by_severity"]["unspecified"] == 1
    assert bugs["open_total"] == 3
    # the active lane named, with its item, so the coordinator sees WHAT each lane holds
    assert any(ln["name"] == "holder" and ln["item"] == "X" for ln in out.data["lanes"])
    # a reviewer up/429 line: the reachability each enabled reviewer is in, up or rate-limited
    assert any(r["reachability"] in ("up", "429") for r in out.data["reviewers"])
    assert out.data["stale_pins"] == []
    assert out.data["interrupted"] == 0
    assert out.data["approvals_pending"] == 0


def test_vigil_cli_text_folds_the_same_facts_into_lines(repo, monkeypatch):
    _seed(repo)
    _quiet_pins_and_reviewers(monkeypatch)
    code, out, err = run_cli(repo, "vigil")
    assert code == 0, (out, err)
    assert "critical" in out and "high" in out
    assert "holder" in out and "X" in out
    assert "up" in out


def test_vigil_has_an_mcp_tool(repo, monkeypatch):
    from ddflow.surfaces.mcp import TOOLS

    assert "ddflow_vigil" in TOOLS
