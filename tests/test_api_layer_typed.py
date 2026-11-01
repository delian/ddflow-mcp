"""The application layer: dispatch mechanism, clearing a field and exit vocabulary.

Split from tests/test_api_layer.py (rule r-small-files); helpers live in api_layer_support.py.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api_layer_support import (
    ARGV_TOOLS_CEILING,
    NOTHING,
    REFUSED,
    _argv_only,
    _typed,
)
from conftest import run_cli

from ddflow import api
from ddflow.core import outcome as O
from ddflow.surfaces.mcp import TOOLS


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
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
    for result in (
        api.update(repo, "T1", api.ItemEdit(title="x")),
        api.update(repo, "NOPE", api.ItemEdit(title="x")),
        api.update(repo, "T1", api.ItemEdit()),
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
    run_cli(repo, "task", "add", "A", "--title", "A fixture", "--globs", "a.py")
    run_cli(repo, "task", "add", "B", "--title", "B fixture", "--globs", "b.py", "--needs", "A")

    api.update(repo, "B", api.ItemEdit(title="renamed"))  # needs untouched
    _code, out, _ = run_cli(repo, "--json", "show", "B")
    assert json.loads(out)["needs"] == ["A"], "an unrelated update cleared needs"

    api.update(repo, "B", api.ItemEdit(needs=[]))  # explicitly cleared
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
    run_cli(repo, "task", "add", "A", "--title", "A fixture", "--globs", "a.py")
    run_cli(repo, "task", "add", "B", "--title", "B fixture", "--globs", "b.py", "--needs", "A")

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
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")

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
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
    result = api.update(repo, "T1", api.ItemEdit())
    assert result.exit == NOTHING, result
    assert not result.ok


def test_asking_whether_an_item_may_complete_does_not_complete_it(repo):
    """An agent that can only ask by ATTEMPTING learns the answer by causing the thing
    it was checking for."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
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


def test_the_reason_is_its_OWN_content_block_never_a_prefix():
    """It used to be prepended to the JSON. That reads well and breaks every machine
    consumer: `json.loads(content[0].text)` fails at character 0.

    `demos/harness.py::jtool` does exactly that, and three of six demo scenarios broke
    silently during the B37 migration — for every tool whose outcome is exit 2 or 3, which
    on a fresh project is most of the read-only ones. The wire-shape comparison missed it
    because it skipped to the first `{` before parsing, which is the accommodation its own
    docstring warns about.

    Both readers are served by two blocks: a machine indexes `content[0]`, a model is shown
    all of them.
    """
    import json as _json

    from ddflow.surfaces.mcp import _outcome_result

    out = _outcome_result(O.refused("k", "lease held by beta", rows=[]))
    # Still JSON at `content[0]` -- and, since B9cf58aaeaa, a refusal's JSON leads with
    # the refusal itself (tests/test_mcp_refusal_shape.py).
    assert _json.loads(out["content"][0]["text"]) == {
        "refusal": {"reason": "lease held by beta", "outcome": "refused", "exit": REFUSED},
        "rows": [],
    }, out["content"][0]["text"]
    assert out["content"][1]["text"] == "lease held by beta", out["content"]
    assert out["_meta"]["exit"] == REFUSED

    # No reason, no second block — an empty one would be a block consumers must skip.
    quiet = _outcome_result(O.ok("k", rows=[1]))
    assert len(quiet["content"]) == 1, quiet["content"]
