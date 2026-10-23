"""The MCP instructions are a charter, not a manual (LS-charter, P-lean-start).

Every spawned agent pays for the `initialize` instructions before its first turn, so they
state the contract only and say where to fetch more; the full workflow is one call away
(`ddflow_help` topic `manual`). Enforcement does not move into this text: it stays in
hooks, leases and gate refusals, so each line below is something a tool will also refuse.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

#: The cap from the research (docs/ddflow/research/lean-start.md section 5, lever 3).
CHARTER_MAX_CHARS = 1500

#: The contract, in order (research section 4); each entry is the text the line must keep.
CONTRACT = (
    "ddflow_brief",  # call it first
    "ddflow_claim",  # claim before editing
    "ask the operator",  # ask, do not guess
    "unavailable",  # a tool that could not run is never `passed`
    "regression test",  # a bug is not closed without one
    "ddflow_recall",  # where to fetch more
    "ddflow_help",
    "ddflow_rule_show",  # D-rules-charter: the full text of ANY rule on demand
)


def _instructions(repo: Path) -> str:
    from ddflow.surfaces.mcp import _instructions as fn

    return fn(repo)


#: Notices the charter adds only while their state holds (crashed worktrees, a drifted AGENTS.md,
#: unwired companions, an unfinished import, setup gaps); they leave the contract text alone.
NOTICES = {
    "recoverable": 0,
    "loops": 0,
    "rules_drift": [],
    "actionable_companions": [],
    "gate_gaps": [],
    "queue_is_empty": False,
    "imported_no_globs": 0,
    "imported_shipped_drift": 0,
    "setup_todo": [],
}
#: The worst case a freshly initialised project shows: every setup gap and unwired companion at once.
FRESH_INIT_MAX = 2300  # measured 2208 bytes: the cap above plus the notices, nothing else


def _base(repo: Path) -> str:
    """The charter with no state notice: what a configured, healthy project is handed."""
    from ddflow.services import prompts as P
    from ddflow.surfaces.mcp import _instruction_vars

    vars_ = {**_instruction_vars(repo), **NOTICES, "tool_tier_note": ""}
    return P.render(P.resolve("mcp_instructions", repo), **vars_).strip()


def test_the_adopted_charter_keeps_every_contract_line_under_the_cap(repo):
    run_cli(repo, "init")
    text = _base(repo)
    assert "as_agent" in text and "PR flow" in text  # identity per call; merge parks in PR flow
    for must in CONTRACT:
        assert must in text, f"the charter dropped {must!r}"
    assert len(text.encode()) <= CHARTER_MAX_CHARS, (
        f"{len(text.encode())} bytes > {CHARTER_MAX_CHARS}:\n{text}"
    )


def test_a_freshly_initialised_project_stays_small_with_every_notice_on(repo):
    run_cli(repo, "init")
    full = _instructions(repo)
    assert len(full.encode()) <= FRESH_INIT_MAX, f"{len(full.encode())} bytes:\n{full}"
    for must in CONTRACT:  # the contract survives every notice being on
        assert must in full, f"a notice displaced {must!r}"
    base = _base(repo)
    assert full.startswith(base[:200])
        "notices outgrew their share"
    )


def test_the_unadopted_charter_is_under_the_cap_too(repo):
    text = _instructions(repo)
    assert "does not use ddflow yet" in text and "ddflow_setup" in text
    assert len(text.encode()) <= CHARTER_MAX_CHARS, f"{len(text.encode())} bytes"


def test_the_charter_points_at_a_manual_that_exists_and_holds_the_rest(repo):
    from ddflow.services import help as H

    run_cli(repo, "init")
    assert "`ddflow_help` topic `manual`" in _instructions(repo)
    manual = H.render_topic("manual", repo)
    # What the charter no longer carries is still reachable.
    for kept in (
        "roborev",
        "codeguide",
        "context7",
        "verbatim",
        "ddflow_review_triage",
        "Exit code 3",
    ):
        assert kept in manual, f"the manual lost {kept!r}"


def test_the_retrieval_line_names_how_to_fetch_any_rule(repo):
    run_cli(repo, "init")
    text = _instructions(repo)
    assert "ddflow_rule_show id=<id>" in text and "ddflow_rule_search" in text


def test_the_session_start_framing_carries_the_contract_in_a_kilobyte(repo):
    """The static text the SessionStart hook adds around the brief (header and footer)."""
    from ddflow.api.setup import SESSION_START_FOOTER, SESSION_START_HEADER

    framing = SESSION_START_HEADER + SESSION_START_FOOTER
    assert len(framing.encode()) <= 1000, len(framing.encode())
    for must in (
        "claim",
        "operator",
        "unavailable",
        "ddflow_brief",
        "ddflow_recall",
        "ddflow_help",
        "ddflow_rule_show",
    ):
        assert must in framing, must
