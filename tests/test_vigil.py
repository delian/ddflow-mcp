"""`ddflow vigil`: one compact health snapshot, one call, one fold.

Regression test for bug B3a86c5cfb9: the coordinator reconstructed its hourly snapshot
via 5+ shell pipelines (bug count+severity, stale pins, active lanes, interrupted,
pending approvals, reviewer reachability). `vigil` returns that whole block in one pass,
reusing the existing verbs -- so this test fails against the unfixed code, which has no
`vigil` at all.
"""

from __future__ import annotations

import importlib

from conftest import run_cli

# The seam-patching tests reach internals: import the MODULE, not the re-exported
# `vigil_snapshot` function (the area module is `reporting.vigil`; the function is named
# `vigil_snapshot` so it never shadows the module).
VV = importlib.import_module("ddflow.api.reporting.vigil")


class _Rev:
    """A reviewer the real classification code reads (name/enabled/base_url are all it asks)."""

    name = "gemini"
    enabled = True
    base_url = "http://127.0.0.1:1"


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


def test_vigil_reports_severities_lanes_and_a_reviewer_line(repo, monkeypatch):
    _seed(repo)
    # Exercise the REAL classification: one reachable, non-degraded reviewer reads "up".
    monkeypatch.setattr(VV.RVM, "load_reviewers", lambda repo: [_Rev()])
    monkeypatch.setattr(VV, "_reviewer_up", lambda url: True)

    out = VV.vigil_snapshot(repo)

    assert out.exit == 0, out.reason
    bugs = out.data["bugs"]
    assert bugs["open_by_severity"]["critical"] == 1
    assert bugs["open_by_severity"]["high"] == 1
    assert bugs["open_by_severity"]["unspecified"] == 1
    assert bugs["open_total"] == 3
    # the active lane named, with its item, so the coordinator sees WHAT each lane holds
    assert any(ln["name"] == "holder" and ln["item"] == "X" for ln in out.data["lanes"])
    # the one-pass contract also carries fixed-in-window and lane-age
    assert bugs["fixed_in_window"] == 0  # the seeded bugs are open, none fixed in window
    assert all("lane_age_s" in ln for ln in out.data["lanes"])
    assert out.data["approvals_pending"] == 0  # a fresh repo has no pending approvals
    # reviewer reachability in DATA: up (reachable) or 429 (quota wall), not stubbed away
    assert any(r["reachability"] in ("up", "429") for r in out.data["reviewers"])
    # the fresh repo has no pin recipes, so `pins --check` cannot run: `stale_pins` is the
    # `None` sentinel (unavailable), never a clean empty [] that would render as "none"
    assert out.data["stale_pins"] is None
    # ... and the reviewer up/429 line renders
    assert "up" in out.data["text"]


def test_vigil_cli_is_one_declaration(repo):
    _seed(repo)
    code, out, err = run_cli(repo, "vigil")
    assert code == 0, (out, err)
    assert "critical" in out and "high" in out
    assert "holder" in out and "X" in out
    assert "interrupted: 0" in out


def test_vigil_has_an_mcp_tool():
    from ddflow.surfaces.mcp import TOOLS

    assert "ddflow_vigil" in TOOLS
