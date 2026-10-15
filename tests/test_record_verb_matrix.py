"""The verb x kind x surface matrix: today's truth, and a ratchet (P-core-records.matrix).

`ddflow.views.record_matrix` probes the live registries (the parser, `TOOLS`, `ddflow.api`)
for what each record kind offers on each surface. These tests pin the gaps it finds as lists
that may only SHRINK, as `tests/test_mcp_parity.py` does for commands: a verb a kind gains
makes its entry stale (delete it); a verb that vanishes, or a new kind with holes, fails.
"""

from __future__ import annotations

import pytest

import ddflow.api as API
from ddflow.api import records as R
from ddflow.surfaces import exemptions as X
from ddflow.surfaces.cli import build_parser
from ddflow.surfaces.commands import viewers_lists as L
from ddflow.surfaces.mcp import TOOLS
from ddflow.views import record_matrix as M

#: (kind, verb) pairs no surface offers. P-core-records closes these; delete an entry the
#: day its verb exists. Never add one.
UNOFFERED: dict[str, tuple[str, ...]] = {
    "bug": (
        "edit",
        "note",
        "history",
        "supersede",
        "split",
        "search",
    ),
    "lesson": (
        "show",
        "edit",
        "note",
        "remove",
        "reopen",
        "history",
        "split",
    ),
    "research": (
        "show",
        "edit",
        "note",
        "remove",
        "reopen",
        "history",
        "supersede",
        "split",
        "search",
    ),
    "memory": (
        "show",
        "edit",
        "note",
        "reopen",
        "history",
        "supersede",
        "split",
        "search",
    ),
    "task": (
        "note",
        "reopen",
        "supersede",
        "search",
    ),
    "phase": (
        "note",
        "reopen",
        "supersede",
        "split",
        "search",
    ),
    "job": (
        "show",
        "edit",
        "note",
        "link",
        "reopen",
        "history",
        "supersede",
        "split",
        "search",
    ),
    "decision": (
        "edit",
        "note",
        "remove",
        "reopen",
        "history",
        "split",
    ),
    "rule": (
        "note",
        "link",
        "reopen",
        "history",
        "supersede",
        "split",
    ),
    "session": (
        "edit",
        "link",
        "remove",
        "reopen",
        "history",
        "supersede",
        "split",
        "search",
    ),
}
#: Verbs offered on some surface of a kind but not on a surface that could carry them.
CROSS_SURFACE: set[tuple[str, str, str]] = {("decision", "search", "mcp")}


def probe() -> M.Probe:
    return M.Probe(build_parser(), TOOLS, X.ROUTED_PATHS, API, L._LISTS, R.KINDS)


@pytest.fixture(scope="module")
def cells():
    return M.matrix(probe())


def _flat(table):
    return {(k, v) for k, vs in table.items() for v in vs}


def test_no_new_verb_is_missing_from_every_surface(cells):
    """The ratchet: no pair outside the baseline, and none left in it that now exists."""
    now = set(M.unoffered(cells))
    baseline = _flat(UNOFFERED)
    assert now <= baseline, f"a verb lost every surface: {sorted(now - baseline)}"
    assert baseline <= now, f"now offered, drop from UNOFFERED: {sorted(baseline - now)}"


def test_no_surface_lacks_a_verb_another_has(cells):
    now = set(M.gaps(cells))
    assert now <= CROSS_SURFACE, f"new cross-surface gap: {sorted(now - CROSS_SURFACE)}"
    assert CROSS_SURFACE <= now, f"closed, drop from CROSS_SURFACE: {sorted(CROSS_SURFACE - now)}"


def test_every_cell_of_every_kind_and_verb_is_answered(cells):
    assert len(cells) == len(M.KINDS) * len(M.VERBS) * len(M.SURFACES)
    assert all(isinstance(v, str) for v in cells.values())


@pytest.mark.parametrize(
    ("kind", "verb", "cli", "mcp", "api"),
    [
        ("bug", "add", "bug found", "ddflow_bug_found", "bug_found"),
        ("bug", "reopen", "bug reopen", "ddflow_bug_invalid (reopen)", "bug_reopen"),
        ("bug", "show", "show", "ddflow_show", "show"),
        ("memory", "remove", "memory forget", "ddflow_memory_forget", "memory_forget"),
        ("task", "split", "split", "ddflow_split", "split"),
        ("lesson", "list", "lesson list", "ddflow_list (lesson)", "view_list"),
    ],
)
def test_known_cells_are_read_from_the_registries(cells, kind, verb, cli, mcp, api):
    assert (cells[(kind, verb, "cli")], cells[(kind, verb, "mcp")]) == (cli, mcp)
    assert cells[(kind, verb, "api")] == api


def test_the_gaps_the_phase_exists_for_are_in_the_matrix(cells):
    """Operator 2026-10-12: a bug cannot be edited, extended with context or its history read."""
    for verb in ("edit", "note", "history"):
        assert not any(cells[("bug", verb, s)] for s in M.SURFACES), verb


def test_a_declared_record_kind_offers_every_verb_it_declares_on_every_surface(cells):
    """The one family on the descriptor (`rule`) has all of its declared verbs, everywhere
    the CLI, MCP and the API can carry them: the descriptor is the source of the cells."""
    assert R.KINDS
    for name, kind in R.KINDS.items():
        for verb in kind.verbs:
            if verb in M.VERBS:
                for surface in ("cli", "mcp", "api"):
                    assert cells[(name, verb, surface)], (name, verb, surface)


def test_a_removed_tool_turns_its_cell_into_a_gap():
    probe_ = probe()
    assert probe_.mcp_cell("bug", "add") == "ddflow_bug_found"
    probe_.tools = {k: v for k, v in probe_.tools.items() if k != "ddflow_bug_found"}
    probe_.routed = {}
    assert probe_.mcp_cell("bug", "add") == ""


def test_hooks_reach_sessions_only(cells):
    hooked = {(k, v) for (k, v, s), c in cells.items() if s == "hooks" and c}
    assert hooked == {("session", "add"), ("session", "note")}


def test_the_rendering_has_a_row_per_kind_and_a_column_per_verb(cells):
    lines = M.render(cells).splitlines()
    assert len(lines) == 2 + len(M.KINDS)
    assert lines[0].count("|") == len(M.VERBS) + 2
    assert next(r for r in lines if r.startswith("| bug ")).split("|")[3].strip() == "CMA"
