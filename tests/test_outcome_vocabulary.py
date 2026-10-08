"""One vocabulary for outcomes (B-uni-outcomes): what "finished" and "live" mean."""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from ddflow.config import Config
from ddflow.core.model import (
    ABANDONED,
    BLOCKED,
    DONE,
    GATE_OUTCOMES,
    OPEN,
    REVIEW,
    RUNNING,
    GateRecord,
    Item,
    State,
)

PKG = Path(__file__).resolve().parents[1] / "ddflow"


@pytest.mark.parametrize(
    ("state", "terminal"),
    [
        (OPEN, False),
        (RUNNING, False),
        (BLOCKED, False),
        (REVIEW, False),
        (DONE, True),
        (ABANDONED, True),
    ],
)
def test_terminal_is_done_or_abandoned(state: str, terminal: bool) -> None:
    assert Item(id="x", kind="task", state=state).terminal is terminal


def _state() -> State:
    st = State()
    for n, removed in (("c", False), ("a", True), ("b", False)):
        st.items[n] = Item(id=n, kind="task", removed=removed)
    return st


def test_live_items_skips_removed_and_keeps_definition_order() -> None:
    st = _state()
    assert [i.id for i in st.live_items()] == ["c", "b"]
    assert list(st.live_by_id()) == ["c", "b"]
    assert st.live_by_id()["b"] is st.items["b"]


#: Sites that still spell "done or abandoned" out, awaiting their slice; only goes down.
OLD_TERMINAL_SPELLINGS = 11
OLD_LIVE_COMPREHENSIONS = 7


def _count(pattern: str) -> int:
    rx = re.compile(pattern)
    return sum(len(rx.findall(p.read_text())) for p in PKG.rglob("*.py"))


def test_terminal_spellings_only_go_down() -> None:
    n = _count(r"\(DONE, ABANDONED\)|\(ABANDONED, DONE\)")
    assert n <= OLD_TERMINAL_SPELLINGS, f"{n} sites; use Item.terminal"


def test_live_comprehensions_only_go_down() -> None:
    n = _count(r"for \w+ in \w+\.items\.values\(\) if not \w+\.removed")
    assert n <= OLD_LIVE_COMPREHENSIONS, f"{n} sites; use State.live_items()"


# -- "gate settled": one table across every place that decides it ---------------------
#
# Five call sites each had their own test of whether a gate's outcome is enough:
# `gates.status` (done/current/complete), `completion.verdict` (required gates must
# have PASSED), the brief's "gates remaining" line, `verify._gates` (the same question
# asked of the record), and `api.gates`' "no outcome yet" ordering check.

GATE = "g1"


def _one_gate(outcome: str, *, required: bool) -> tuple[State, Config]:
    cfg = Config.load()
    cfg.gates.task_pipeline = [GATE]
    cfg.gates.required = [GATE] if required else []
    cfg.gates.require_outcome = True
    st = State()
    it = Item(id="T", kind="task")
    if outcome:
        it.gates[GATE] = GateRecord(gate=GATE, outcome=outcome, reason="r")
    st.items["T"] = it
    return st, cfg


def _sites(outcome: str, *, required: bool, repo: Path) -> dict[str, bool]:
    """For each site: does it treat the gate as SATISFIED (nothing left to do for it)?"""
    from ddflow.services import completion
    from ddflow.services import verify as V
    from ddflow.services.gates import status
    from ddflow.views.markdown import _brief_current

    st, cfg = _one_gate(outcome, required=required)
    s = status(st, cfg, "T")
    v = completion.verdict(st, cfg, "T", repo=repo)
    out: list[str] = []
    _brief_current(out, st, cfg, "T", repo)
    led = {
        "gates": {GATE: {"outcome": outcome, "reason": "r"}} if outcome else {},
        "forced": False,
        "overridden": [],
    }
    claim = V._gates(cfg, led, [GATE])
    return {
        "status.done": GATE in s.done,
        "status.complete": s.complete,
        "verdict": not any(GATE in b for b in v.blockers),
        "brief": any("gates remaining: none" in line for line in out),
        "verify": claim.status in {"ok", "warn"},
    }


def _pipeline_ok(outcome: str, required: bool) -> bool:
    """Nothing is left to DO for this gate: passed, or skipped where it is not required."""
    return outcome == "passed" or (outcome == "skipped" and not required)


def _completion_ok(outcome: str, required: bool) -> bool:
    """The gate does not stop the item completing: it has an outcome, and a required gate
    PASSED. A failed gate that is not required is allowed through (D-failed-critic-not-
    blocking), which is why this is not `_pipeline_ok`."""
    return bool(outcome) and (not required or outcome == "passed")


@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("outcome", ["", *GATE_OUTCOMES])
def test_every_site_agrees_on_a_settled_gate(outcome: str, required: bool, repo: Path) -> None:
    site = _sites(outcome, required=required, repo=repo)
    pipeline = _pipeline_ok(outcome, required)
    completion = _completion_ok(outcome, required)
    assert site == {
        "status.done": pipeline,
        "status.complete": pipeline,
        "brief": pipeline,
        "verdict": completion,
        "verify": completion,
    }


def test_gate_outcome_vocabulary_is_unchanged() -> None:
    """The enum is the old tuple, marks and exits, byte for byte."""
    from ddflow.api.gates import OUTCOME_EXIT
    from ddflow.core.records import OUTCOME_MARK

    assert GATE_OUTCOMES == ("passed", "failed", "unavailable", "partial", "skipped")
    assert OUTCOME_MARK == {
        "passed": "x",
        "failed": "!",
        "unavailable": "?",
        "partial": "~",
        "skipped": "-",
        "": " ",
    }
    assert OUTCOME_EXIT == {"passed": 0, "skipped": 0, "failed": 1, "unavailable": 2, "partial": 2}


@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("outcome", ["", "bogus", *GATE_OUTCOMES])
def test_settled_is_satisfies_and_never_for_a_missing_outcome(outcome: str, required: bool) -> None:
    from ddflow.core.records import GateOutcome

    want = outcome == "passed" or (outcome == "skipped" and not required)
    assert GateOutcome.settled(outcome, required) is want
    assert not GateOutcome.settled("", required)


def test_gate_status_render_draws_the_shared_marks() -> None:
    from ddflow.services.gates.outcomes import GateStatus

    st, cfg = _one_gate("skipped", required=False)
    from ddflow.services.gates import status

    s = status(st, cfg, "T")
    assert "[-] g1" in s.render()
    s.rows = [("a", "passed"), ("b", "failed"), ("c", "unavailable"), ("d", "partial"), ("e", "")]
    assert [line.strip()[:3] for line in s.render().splitlines()] == [
        "[x]",
        "[!]",
        "[?]",
        "[~]",
        "[ ]",
    ]
    assert isinstance(s, GateStatus)


# -- exit codes: one home ---------------------------------------------------------------

#: Modules still defining an exit code as an int literal (the ratchet only goes down);
#: `core/outcome.py` is the home.
EXIT_NAME = re.compile(r"^(OK|FAIL|FAILED|NOTHING|REFUSED|UNAVAILABLE|EXIT_[A-Z_]+)$")


def _is_int(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, int)


def _pairs(target: ast.AST, value: ast.AST | None) -> Iterator[tuple[ast.AST, ast.AST | None]]:
    """Each (name, value) an assignment binds, tuples unpacked pairwise."""
    if isinstance(target, ast.Tuple) and isinstance(value, ast.Tuple):
        for t, v in zip(target.elts, value.elts, strict=False):
            yield from _pairs(t, v)
    elif isinstance(target, ast.Tuple):
        for t in target.elts:
            yield from _pairs(t, None)
    else:
        yield target, value


def _exit_literals() -> list[str]:
    sites = []
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(PKG).as_posix()
        if rel == "core/outcome.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Assign):
                pairs = [pr for t in node.targets for pr in _pairs(t, node.value)]
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                pairs = [(node.target, node.value)]
            else:
                continue
            if any(
                isinstance(t, ast.Name) and EXIT_NAME.match(t.id) and _is_int(v) for t, v in pairs
            ):
                sites.append(f"{rel}:{node.lineno}")
    return sites


#: export/write.py's EXIT_STALE (awaiting its slice) and review.py's REVIEWED/ERROR/
#: UNAVAILABLE/PARTIAL, a result vocabulary of its own that shares four small ints.
OLD_EXIT_LITERALS = 2


def test_exit_codes_are_defined_once() -> None:
    sites = _exit_literals()
    assert len(sites) <= OLD_EXIT_LITERALS, f"import from ddflow.core.outcome instead: {sites}"


def test_the_reexported_exit_names_are_the_outcome_values() -> None:
    from ddflow.core import outcome as O
    from ddflow.services import upstream_delivery as U
    from ddflow.services.export import query
    from ddflow.surfaces import context

    assert (context.OK, context.FAIL, context.NOTHING, context.REFUSED) == (0, 1, 2, 3)
    assert (context.OK, context.FAIL, context.NOTHING, context.REFUSED) == (
        O.OK,
        O.FAIL,
        O.NOTHING,
        O.REFUSED,
    )
    assert (query.EXIT_UNAVAILABLE, query.EXIT_REFUSED) == (2, 3)
    assert (U.OK, U.FAILED, U.UNAVAILABLE, U.REFUSED) == (0, 1, 2, 3)


@pytest.mark.parametrize(
    "line", ["EXIT_FOO: int = 5", "OK, X = 0, 'x'", "UNAVAILABLE = 2", "A, REFUSED = 'a', 3"]
)
def test_the_exit_literal_scan_sees_every_spelling(line: str, tmp_path: Path, monkeypatch) -> None:
    pkg = tmp_path / "ddflow"
    pkg.mkdir()
    (pkg / "mod.py").write_text(line + "\n")
    monkeypatch.setattr(sys.modules[__name__], "PKG", pkg)
    assert _exit_literals() == ["mod.py:1"]


# -- services return a named verdict, not a bare (int, str) -----------------------------


def test_verdict_is_a_tuple_with_named_parts() -> None:
    from ddflow.core.outcome import FAIL, Verdict

    v = Verdict(FAIL, "why")
    assert v == (1, "why") and (v.exit, v.message) == (1, "why")
    assert Verdict(0) == (0, "")
    code, msg = v
    assert (code, msg) == (1, "why")


def test_enforce_checks_return_verdicts(repo: Path) -> None:
    from ddflow.services import enforce as E

    for got in (
        E.check_commit(repo),
        E.check_views(repo),
        E.check_docs(repo),
        E.check_forbidden_trailers("s\n", []),
    ):
        assert type(got).__name__ == "Verdict" and got.exit in (0, 1, 2, 3)


def test_enforce_names_its_exit_codes() -> None:
    """No `return 1, msg` or `return (1, msg)`, however spaced: a check's exit comes from
    core/outcome, so 2 cannot be typed as 0. And no check is annotated as a bare
    (int, str) tuple."""
    tree = ast.parse((PKG / "services" / "enforce.py").read_text())
    bare = [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Return)
        and isinstance(n.value, ast.Tuple)
        and n.value.elts
        and isinstance(n.value.elts[0], ast.Constant)
        and isinstance(n.value.elts[0].value, int)
    ]
    assert not bare, f"literal exit codes at lines {bare}; return Verdict(OK|FAIL|NOTHING, ...)"
    tupled = [
        n.name
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef)
        and n.name.startswith(("check_", "_check_", "_verdict"))
        and n.returns is not None
        and ast.unparse(n.returns)
        .replace(" ", "")
        .lower()
        .startswith(("tuple[int,str]", "tuple[int,"))
    ]
    assert not tupled, f"{tupled} return a bare (int, str); return Verdict"


def test_classify_exit_returns_gate_outcomes() -> None:
    from ddflow.core.records import GateOutcome
    from ddflow.services.gates import GateDef, classify_exit

    got = classify_exit(GateDef(id="g"), 0, "")
    assert got == ("passed", "") and got.outcome is GateOutcome.PASSED
