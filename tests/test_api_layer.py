"""The application layer, and the migration off the string path.

`surfaces/mcp.py` reached `surfaces/cli.py` — a protocol adapter depending on a
presentation layer — over ARGV. Typed arguments were flattened to strings, re-parsed by
argparse, and the result recovered by scraping stdout. Two costs, both visible in the
code that worked around them: `_opt(clearable=True)` existed only to rebuild the
"absent vs empty" distinction argv destroyed, and `_run_cli` swapped process-global
streams per call, which is not reentrant in a server for a tool whose purpose is
parallel agents.

Migration, not rewrite. A tool with an `api` entry goes through the typed path; the
rest still go through argv, and the count of those may only decrease.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow import api
from ddflow.core import outcome as O
from ddflow.surfaces.mcp import TOOLS

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3

#: Tools still dispatched by flattening arguments to argv. May only ever DECREASE.
#: Raising it means a new tool was added on the path this layer exists to replace.
ARGV_TOOLS_CEILING = 62


def _typed() -> list[str]:
    return sorted(n for n, s in TOOLS.items() if "api" in s)


def _argv_only() -> list[str]:
    """Tools that actually flatten to argv — keyed on the `argv` entry, NOT on the
    absence of `api`.

    The difference is not cosmetic. A third dispatch kind exists (`identify`, which
    mutates the connection and calls neither layer), and "not typed" counted it as a
    string-path tool — which would have forced the ceiling UP to admit a tool that does
    not use the string path at all. A ratchet you raise to accommodate something it was
    never measuring has stopped measuring anything.
    """
    return sorted(n for n, s in TOOLS.items() if "argv" in s)


def test_every_tool_has_exactly_one_dispatch_mechanism():
    """The guard that makes the count above meaningful.

    With three kinds and no check, a tool carrying neither key is silently unreachable
    -- its call falls through every branch -- and one carrying two is dispatched by
    whichever branch is tested first. NEITHER is visible to a ratchet counting one key.
    """
    kinds = ("api", "argv", "identify")
    for name, spec in TOOLS.items():
        have = [k for k in kinds if k in spec]
        assert len(have) == 1, f"{name} declares {have or 'no'} dispatch; exactly one is required"


def test_the_string_path_only_ever_shrinks():
    """An allowlist that may not grow, like every other ratchet here. Without it the
    layer becomes a thing two tools use and sixty ignore."""
    assert len(_argv_only()) <= ARGV_TOOLS_CEILING, (
        f"{len(_argv_only())} tools still flatten to argv, ceiling is "
        f"{ARGV_TOOLS_CEILING}. A NEW tool must go through `api`; lower the ceiling "
        f"when you migrate one."
    )
    assert _typed(), "no tool uses the typed path at all, so it is decoration"


def test_every_api_function_returns_an_outcome(repo):
    """One description of a result, from which both surfaces derive their view —
    rather than each surface describing the same thing independently, which is the
    shape that printed a coverage gap to humans only."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    for result in (
        api.update(repo, "T1", title="x"),
        api.update(repo, "NOPE", title="x"),
        api.update(repo, "T1"),
        api.completion_verdict(repo, "T1"),
    ):
        assert isinstance(result, O.Outcome), result
        assert result.kind, "an Outcome with no kind cannot be rendered"


# -- the distinction argv destroyed ------------------------------------------------------


def test_clearing_a_field_is_distinguishable_from_not_touching_it(repo):
    """The bug `clearable` was invented to work around, stated as behaviour.

    `None` leaves a field alone; `[]` clears it. Over argv both became `""`, so
    `ddflow_update(id="X", needs="")` silently did nothing while the shell form cleared
    it — and breaking a dependency cycle is exactly the operation that needs it, and
    the one the loop detector tells you to perform.
    """
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "A", "--globs", "a.py")
    run_cli(repo, "task", "add", "B", "--globs", "b.py", "--needs", "A")

    api.update(repo, "B", title="renamed")  # needs untouched
    _code, out, _ = run_cli(repo, "--json", "show", "B")
    assert json.loads(out)["needs"] == ["A"], "an unrelated update cleared needs"

    api.update(repo, "B", needs=[])  # explicitly cleared
    _code, out, _ = run_cli(repo, "--json", "show", "B")
    assert json.loads(out)["needs"] == [], "an explicit clear did nothing"


def test_clearing_a_field_over_mcp_works_on_whichever_path_is_live(repo):
    """A behavioural regression test, and deliberately NOT evidence about the layer.

    Clearing used to work on the argv path too, via an `_opt(clearable=True)` flag that
    existed solely to rebuild the absent-vs-empty distinction argv erased. That flag has
    since been removed along with its last caller, so today this exercises the typed
    path — but the test is still written as a BEHAVIOURAL guard rather than as evidence
    about the layer. `test_the_dispatcher_actually_uses_the_typed_path` is the one that
    speaks to the architecture; this one says only that clearing a field works.
    """
    from ddflow.surfaces.mcp import Server

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "A", "--globs", "a.py")
    run_cli(repo, "task", "add", "B", "--globs", "b.py", "--needs", "A")

    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_update", "arguments": {"id": "B", "needs": ""}},
        }
    )
    assert reply["result"]["isError"] is False, reply
    _code, out, _ = run_cli(repo, "--json", "show", "B")
    assert json.loads(out)["needs"] == [], "clearing over MCP still does nothing"


def test_the_dispatcher_actually_uses_the_typed_path(repo):
    """The migration is the point; a typed entry nothing dispatches to is decoration.

    Proven by making the `api` entry raise: if the reply carries that failure, the
    dispatcher went through it. The argv path would have quietly succeeded.
    """
    from ddflow.surfaces.mcp import Server

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")

    def boom(_repo, _args, _agent):
        raise ValueError("the typed path was taken")

    original = TOOLS["ddflow_update"]["api"]
    TOOLS["ddflow_update"]["api"] = boom
    try:
        reply = Server(repo).handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "ddflow_update", "arguments": {"id": "T1", "title": "x"}},
            }
        )
    finally:
        TOOLS["ddflow_update"]["api"] = original
    assert "the typed path was taken" in reply["result"]["content"][0]["text"], reply


def test_an_update_that_changes_nothing_says_so(repo):
    """`2` is not `0`. "You asked me to change nothing" is an answer, and returning
    success for it hides a caller that forgot to pass a field."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    result = api.update(repo, "T1")
    assert result.exit == NOTHING, result
    assert not result.ok


def test_asking_whether_an_item_may_complete_does_not_complete_it(repo):
    """An agent that can only ask by ATTEMPTING learns the answer by causing the thing
    it was checking for."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    result = api.completion_verdict(repo, "T1")
    assert result.exit == REFUSED, result
    assert result.data["blockers"]
    _code, out, _ = run_cli(repo, "--json", "show", "T1")
    assert json.loads(out)["state"] != "done", "asking completed it"


# -- the typed path keeps the exit-code vocabulary -----------------------------------------


def test_a_refusal_is_not_an_error_on_the_typed_path():
    """Exit 2 and 3 are RESULTS a model must read and act on; only 1 is a failure.
    That rule is the whole exit vocabulary and it must not change with the path."""
    from ddflow.surfaces.mcp import _outcome_result

    assert _outcome_result(O.refused("k", "held")).get("isError") in (False, None)
    assert _outcome_result(O.nothing("k", "none")).get("isError") in (False, None)
    assert _outcome_result(O.failed("k", "broke"))["isError"] is True


def test_the_reason_leads_the_body_so_a_reader_gets_it_first():
    from ddflow.surfaces.mcp import _outcome_result

    body = _outcome_result(O.refused("k", "lease held by beta"))["content"][0]["text"]
    assert body.splitlines()[0] == "lease held by beta", body[:120]
