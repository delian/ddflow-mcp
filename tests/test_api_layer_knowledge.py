"""The application layer: defaults in one place and the rigour rules.

Split from tests/test_api_layer.py (rule r-small-files); helpers live in api_layer_support.py.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from api_layer_support import (
    FAIL,
    OK,
)
from conftest import run_cli

from ddflow import api

# -- defaults live in ONE place -----------------------------------------------------------

#: (subcommand path, argparse dest) -> the api constant that must equal it.
#:
#: Every row here is a bug that shipped. Migrating a command to the typed layer moves the
#: call OFF argparse, so any default that lived only in the parser is silently replaced by
#: whatever the new signature happens to say — and a falsy literal is the natural thing to
#: write. Four were dropped in one session:
#:
#:   next --kind            "task"          -> ""      `ddflow_next` returned an EMPTY queue
#:   phase/task --priority  100             -> 0       everything over MCP filed at top priority
#:   brief --check-recovery True            -> False   crashed work no longer surfaced
#:   render --out           "docs/ddflow"   -> ""      views written to the repo root
#:
#: Only the first was caught by the wire-shape comparison, because only it changed the body
#: in the fixture's state. The other three are invisible to a comparison of two surfaces
#: that now share the same wrong default — which is the same lesson as "parity is not
#: correctness", one layer down.
DEFAULT_SOURCES: list[tuple[tuple[str, ...], str, str]] = [
    (("next",), "kind", "DEFAULT_NEXT_KIND"),
    (("phase", "add"), "priority", "DEFAULT_PRIORITY"),
    (("brief",), "check_recovery", "DEFAULT_CHECK_RECOVERY"),
    (("render",), "out", "DEFAULT_RENDER_DIR"),
]


@pytest.mark.parametrize(("path", "dest", "constant"), DEFAULT_SOURCES)
def test_the_parser_and_the_api_agree_on_every_default(path, dest, constant):
    import argparse as _argparse

    from ddflow.surfaces.cli import build_parser

    node = build_parser()
    for part in path:
        sub = next(a for a in node._actions if isinstance(a, _argparse._SubParsersAction))
        assert part in sub.choices, f"no subcommand {part!r}"
        node = sub.choices[part]
    found = {a.dest: a.default for a in node._actions}
    assert dest in found, f"{'/'.join(path)} has no --{dest.replace('_', '-')}"
    assert found[dest] == getattr(api, constant), (
        f"{'/'.join(path)} --{dest.replace('_', '-')} defaults to {found[dest]!r} but "
        f"api.{constant} is {getattr(api, constant)!r}. The default must have ONE home, "
        f"or the typed path and the argv path disagree without anything saying so."
    )


def test_every_api_default_constant_is_actually_used_by_a_signature():
    """A constant the functions do not use is documentation, not a default.

    `task_add(priority=None)` is deliberately absent: with none given the task takes its band
    (`services.priority.band`, DX-default-priority-for-dx), and only the generic task falls
    back to DEFAULT_PRIORITY."""
    import inspect

    pairs = [
        (api.next_item, "kind", api.DEFAULT_NEXT_KIND),
        (api.phase_add, "priority", api.DEFAULT_PRIORITY),
        (api.brief, "check_recovery", api.DEFAULT_CHECK_RECOVERY),
        (api.render, "out_dir", api.DEFAULT_RENDER_DIR),
    ]
    for fn, param, want in pairs:
        got = inspect.signature(fn).parameters[param].default
        assert got == want, f"api.{fn.__name__}({param}=...) defaults to {got!r}, not {want!r}"


def test_each_default_VALUE_is_the_one_the_behaviour_needs(repo):
    """The defaults, asserted by what they DO.

    `test_the_parser_and_the_api_agree_on_every_default` is deliberately kept above, but
    it is weak and this test exists because mutation testing proved it: once the parser
    imports the constant, changing the constant changes BOTH sides and they still agree.
    A ratchet that cannot fail is the vacuous-pass class wearing a badge, and the only
    honest guard on a default's VALUE is the behaviour that depends on it.
    """

    run_cli(repo, "init")

    # `--kind` -> "task". A phase is an umbrella; "work on P1" is not an instruction.
    run_cli(repo, "phase", "add", "P1", "--title", "P")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--phase", "P1", "--globs", "a.py")
    offered = api.next_item(repo)
    assert [i["id"] for i in offered.data["ready"]] == ["T1"], (
        f"the default kind no longer offers tasks: {offered.data['ready']}"
    )

    # `--priority` -> the MIDDLE of the range, so later work can be pushed either way.
    # At 0 every item created over MCP would outrank everything filed from the CLI.
    _code, shown, _ = run_cli(repo, "--json", "show", "T1")
    assert json.loads(shown)["priority"] == 100, "a task no longer defaults to mid-priority"

    # `--check-recovery` -> True. The moment an agent most needs to know a previous agent
    # crashed is the moment it is about to start work.
    import inspect

    assert inspect.signature(api.brief).parameters["check_recovery"].default is True

    # `--out` -> docs/ddflow, not the repo root. Writing generated views over the top of
    # a project is not a default anyone would choose deliberately.
    out = api.render(repo)
    assert out.data["files"], "render wrote nothing"
    assert all("docs/ddflow" in f for f in out.data["files"]), out.data["files"]


# -- the rules about rigour, which were never themselves verified --------------------------
#
# Every refusal in `api/knowledge.py` survived mutation: the verdict vocabulary, the
# CONFIRMED-needs-a-probe rule, the bug-needs-a-regression-test rule, recall's per-source
# isolation and history's Lamport ordering. Five rules the project enforces on its users
# and had never once enforced on itself.


def test_a_research_note_must_carry_a_real_verdict(repo):
    """ "A note with no verdict is a literature summary, not research."""

    run_cli(repo, "init")
    for bad in ("", "MAYBE", "confirmed", "TRUE"):
        out = api.research_add(repo, api.ResearchFinding(question="does X help?", verdict=bad))
        assert out.exit == FAIL, f"{bad!r} was accepted as a verdict"
        assert "CONFIRMED, REFUTED or THEORETICAL" in out.reason


def test_a_CONFIRMED_verdict_requires_the_probe_that_confirmed_it(repo):
    """ "A verdict with no probe behind it is an opinion."

    The rule about probes, which had no probe. THEORETICAL is the honest label when none
    was possible, and it is accepted without one — that asymmetry IS the rule.
    """

    run_cli(repo, "init")
    for verdict in ("CONFIRMED", "REFUTED"):
        bare = api.research_add(repo, api.ResearchFinding(question="q", verdict=verdict))
        assert bare.exit == FAIL, f"{verdict} accepted with no probe"
        assert "opinion" in bare.reason

        with_probe = api.research_add(
            repo,
            api.ResearchFinding(question="q", verdict=verdict, probe="pytest -k thing"),
        )
        assert with_probe.exit == OK, with_probe

    theoretical = api.research_add(repo, api.ResearchFinding(question="q", verdict="THEORETICAL"))
    assert theoretical.exit == OK, "THEORETICAL is how you record what you could not probe"


def test_a_bug_cannot_be_closed_without_a_regression_test(repo):
    """The rule about regression tests, which had no regression test.

    "Write the test, watch it FAIL against the unfixed code, then close." Nothing
    enforced it on this project's own bug records until now.
    """

    run_cli(repo, "init")
    found = api.bug_found(repo, summary="the fold drops seq")
    bug = found.data["id"]

    bare = api.bug_fixed(repo, bug)
    assert bare.exit == FAIL, bare
    assert "regression-test" in bare.reason

    _code, shown, _ = run_cli(repo, "--json", "status")
    assert json.loads(shown)["open_bugs"] == 1, "a refused close marked the bug fixed"

    # The named test must exist (B855e3cac54): an invented node id no longer closes a bug.
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_events.py").write_text("def test_seq():\n    pass\n")
    closed = api.bug_fixed(repo, bug, regression_test="tests/test_events.py::test_seq")
    assert closed.exit == OK, closed
    _code, shown, _ = run_cli(repo, "--json", "status")
    assert json.loads(shown)["open_bugs"] == 0


def test_one_unreadable_source_does_not_take_the_whole_recall_down(repo):
    """The value of recall is the UNION.

    "The lessons table is corrupt" is not a reason to withhold the decisions — and an
    agent that gets nothing back concludes the project remembers nothing, which is the
    opposite of true.
    """
    from ddflow.infra import store as store_mod

    run_cli(repo, "init")
    run_cli(
        repo,
        "decision",
        "add",
        "--id",
        "D1",
        "--title",
        "use pluggy",
        "--decision",
        "every stage is a Step",
    )
    run_cli(repo, "lesson", "add", "--title", "pluggy first", "--rule", "register steps")

    real = store_mod.Store.search

    def flaky(self, table, query, limit):
        if table == "lessons":
            raise RuntimeError("this table is corrupt")
        return real(self, table, query, limit)

    store_mod.Store.search = flaky
    try:
        out = api.recall(repo, "pluggy")
    finally:
        store_mod.Store.search = real

    assert out.exit == OK, "one bad source took the whole recall down"
    assert "decisions" in out.data["results"], out.data["results"].keys()
    assert "lessons" not in out.data["results"]


def test_history_is_ordered_by_lamport_clock_not_wall_time(repo):
    """ "Two agents have two clocks, and sorting a merged history by timestamp interleaves
    them wrongly while looking perfectly plausible."

    Built by writing a shard whose wall-clock timestamps run BACKWARDS against its Lamport
    clock — which is exactly what a second machine with a skewed clock produces.
    """

    run_cli(repo, "init")
    shard = (repo / ".ddflow" / "events") / "other-agent.jsonl"
    rows = [
        {
            "id": "e1",
            "ts": "2026-09-26T12:00:00",
            "lamport": 900,
            "agent": "beta",
            "kind": "item.blocked",
            "subject": "T9",
            "data": {"reason": "first by lamport"},
        },
        {
            "id": "e2",
            "ts": "2026-09-26T09:00:00",
            "lamport": 901,
            "agent": "beta",
            "kind": "item.blocked",
            "subject": "T9",
            "data": {"reason": "second by lamport"},
        },
    ]
    shard.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    out = api.history(repo, item="T9")
    order = [e["data"]["reason"] for e in out.data["events"]]
    assert order == ["second by lamport", "first by lamport"], (
        f"newest-first by Lamport should put e2 before e1; got {order}. Sorting by `ts` "
        f"reverses them, and that is precisely the skewed-clock case."
    )
