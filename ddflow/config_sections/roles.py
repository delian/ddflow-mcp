"""The `[roles]` section: the project's rules for a role, handed over by `ddflow brief --role R`.

A role pack is data: a list of lines per role, each a plain sentence or `rule:<id>` (rendered
from that rule record's title). Override a pack in any config layer; the default packs below
are the project's standing rules for a phase closer, a fixer, a researcher and a reviewer.
Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob


def _whole(v: object) -> str:
    ok = isinstance(v, int) and not isinstance(v, bool) and v >= 0
    return "" if ok else "a whole number of zero or more"


def _lines(v: object) -> str:
    ok = isinstance(v, list) and all(isinstance(x, str) for x in v)
    return "" if ok else "a list of strings"


@declare("roles")
@dataclass
class RolesConfig:
    """Role packs: what a coordinator would otherwise retype to an agent starting a role."""

    budget_tokens: int = knob(
        600,
        doc="Most tokens (about four characters each) a role pack may take; a longer pack is cut at a line boundary and says so. 0 means no cap.",
        check=_whole,
    )
    phase_closer_rules: list[str] = knob(
        factory=lambda: [
            "Tests first: a regression test that fails against the unfixed code, then the change.",
            "Ratchet offenders are read by name (architecture interfaces, constants ratchet, every family of the dependency guard); never add a baseline entry.",
            "Run the suite detached with the maximum timeout: DDFLOW_CI_SUITE_WAIT_S=21600 setsid nohup ddflow gate run ID ci --timeout 21600, with a heartbeat loop you stop by pid; commit nothing while it runs.",
            "Approvals: file the request from your directory, report its Q id to the coordinator and stop on that item; re-run with --approval once relayed (single use, bound to the command, state and directory). Never approve anything yourself.",
            "Pins: one pins recipe per approval, each fresh after the previous is committed; audit each diff and commit only what your work caused.",
            "Docs-wave stop: when the phase's remaining work is inherited docs-wave needs, stop and report; do not lift the need.",
            "Never push. Finish means nothing ahead of main for your worktree and your event log committed.",
        ],
        doc="The phase-closer role pack: one line per rule, a plain sentence or `rule:<id>` (the title of that rule record). Replaces the default list in any config layer.",
        check=_lines,
    )
    fixer_rules: list[str] = knob(
        factory=lambda: [
            "Tests first: a regression test that fails against the unfixed code, then the fix; a bug is filed before it is fixed.",
            "Never skip or weaken a test, raise a limit or add a ratchet baseline entry; read ratchet offenders by name.",
            "Claim before you edit and lease only the files you need; if a claim is refused, report who holds what and take the next item.",
            "Never push; finish means nothing ahead of main for your worktree and your event log committed.",
        ],
        doc="The fixer role pack: one line per rule, a plain sentence or `rule:<id>`.",
        check=_lines,
    )
    researcher_rules: list[str] = knob(
        factory=lambda: [
            "Read-only: no edits and no claims; state a falsifiable claim, run the cheapest test that could refute it and record the verdict (CONFIRMED, REFUTED or THEORETICAL) with the command and its output.",
            "File every defect found as a bug after `ddflow similar`; never only work around it.",
            "Record the operator's words verbatim and a lesson after any surprise.",
        ],
        doc="The researcher role pack: one line per rule, a plain sentence or `rule:<id>`.",
        check=_lines,
    )
    reviewer_rules: list[str] = knob(
        factory=lambda: [
            "Try to refute the change; a tool or reviewer that could not run is recorded unavailable, never passed.",
            "Prefer a reviewer from another model family; the author's own model never counts.",
            "Never edit files; record each finding with its location and severity; only a new confirmed HIGH stops a merge.",
        ],
        doc="The reviewer role pack: one line per rule, a plain sentence or `rule:<id>`.",
        check=_lines,
    )
