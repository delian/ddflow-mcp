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
import pytest
from conftest import run_cli

from ddflow import api
from ddflow.core import outcome as O
from ddflow.surfaces.mcp import TOOLS

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3

#: Tools still dispatched by flattening arguments to argv. May only ever DECREASE.
#: Raising it means a new tool was added on the path this layer exists to replace.
ARGV_TOOLS_CEILING = 47


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


# -- B37: one answer, two presentations -------------------------------------------------


#: MCP tool -> the CLI argv whose `--json` output it must reproduce EXACTLY.
#: Every migration adds a row. The point is that a tool's wire shape is a CONTRACT its
#: consumers depend on, and B37 exists to remove a duplicated rendering, not to redefine
#: contracts -- `ddflow_loops` silently went from a JSON array to an object and broke two
#: demo scenarios before this existed.
#: tool -> (the CLI argv whose `--json` body it must reproduce, the MCP arguments).
#:
#: Both halves are needed because the two surfaces take their input differently: the
#: CLI puts an id in argv, the MCP call puts it in `arguments`. The first version of
#: this map carried argv only and called every tool with `{}`, which silently made any
#: tool REQUIRING an argument fail with "missing required argument" instead of
#: comparing anything — a shape test that never reached the shape.
MIGRATED_WIRE_SHAPES: dict[str, tuple[list[str], dict[str, object]]] = {
    "ddflow_loops": (["loops"], {}),
    "ddflow_progress": (["progress"], {}),
    "ddflow_decision_list": (["decision", "list"], {}),
    "ddflow_decision_show": (["decision", "show", "D1"], {"id": "D1"}),
    "ddflow_decision_applicable": (["decision", "applicable", "T1"], {"id": "T1"}),
    "ddflow_workflow": (["workflow"], {}),
    "ddflow_status": (["status"], {}),
    "ddflow_rebuild": (["rebuild"], {}),
    "ddflow_show": (["show", "T1"], {"id": "T1"}),
    "ddflow_recover": (["recover"], {}),
}

#: Migrated tools whose body CANNOT be compared by invoking both surfaces, because
#: invoking them twice is not the same as invoking them once.
#:
#: They WRITE. The second call sees the state the first produced, and where the id is
#: auto-generated it is content-addressed with a nanosecond stamp, so the two bodies
#: differ by construction. Listing them here — rather than letting them fall out of the
#: map unnoticed — is what keeps `test_every_typed_tool_has_a_wire_shape_row` honest:
#: an unlisted tool is a missing contract, and a listed one is a deliberate exemption
#: with a reason. Each still needs its own behavioural test.
WRITES_NOT_COMPARABLE = {
    "ddflow_update",
    "ddflow_workflow_pipeline",
    "ddflow_workflow_gate",
    "ddflow_workflow_drop",
    "ddflow_decision_add",
    "ddflow_decision_supersede",
}


@pytest.mark.parametrize("tool", sorted(MIGRATED_WIRE_SHAPES))
def test_a_migrated_tool_reproduces_its_CLI_json_exactly(repo, tool):
    """One test for every migration, present and future.

    Compares the two payloads WITHOUT indexing into either. The bespoke version of this
    reached for `["findings"]` on the MCP side, which validated the contents while
    accommodating the exact shape change it was written to prevent -- so the
    generalisation is not tidiness, it is the thing that makes the check honest.
    """
    import json as _json

    from ddflow.surfaces.mcp import Server

    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "P")
    run_cli(repo, "task", "add", "T1", "--phase", "P1", "--globs", "a.py")
    run_cli(repo, "task", "add", "A", "--needs", "B")
    run_cli(repo, "task", "add", "B", "--needs", "A")  # a cycle, so findings exist
    # A decision with an EXPLICIT id, governing T1's globs, so the decision tools have
    # something to return. An auto id would be content-addressed and unrepeatable, and
    # a comparison of two empty bodies passes without proving anything.
    run_cli(
        repo,
        "decision",
        "add",
        "--id",
        "D1",
        "--title",
        "D",
        "--decision",
        "use the typed layer",
        "--globs",
        "a.py",
    )

    argv, arguments = MIGRATED_WIRE_SHAPES[tool]
    _code, cli_out, _err = run_cli(repo, "--json", *argv)
    from_cli = _json.loads(cli_out)

    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
    )
    text = reply["result"]["content"][0]["text"]
    start = min((i for i in (text.find("["), text.find("{")) if i != -1), default=-1)
    assert start != -1, f"{tool} returned no JSON body: {text[:200]}"
    from_mcp = _json.loads(text[start:])

    assert type(from_mcp) is type(from_cli), (
        f"{tool}: the MCP body is a {type(from_mcp).__name__} and the CLI's is a "
        f"{type(from_cli).__name__} — a migration changed the wire shape"
    )
    assert from_mcp == from_cli, f"{tool}: the surfaces disagree\nCLI: {from_cli}\nMCP: {from_mcp}"


def test_every_typed_tool_has_a_wire_shape_row():
    """A migration without a row is a migration nothing checks. The map is the ratchet:
    `ARGV_TOOLS_CEILING` counts what is left, this covers what has moved."""
    typed = {n for n, s in TOOLS.items() if "api" in s}
    unchecked = typed - set(MIGRATED_WIRE_SHAPES) - WRITES_NOT_COMPARABLE
    assert not unchecked, (
        f"migrated with no wire-shape row: {sorted(unchecked)}. Add one to "
        f"MIGRATED_WIRE_SHAPES so the contract is checked."
    )


def test_a_migrated_tool_gives_both_surfaces_the_SAME_data(repo):
    """The property the typed layer exists for, asserted rather than assumed.

    `cmd_loops` used to build its JSON body and its human paragraph independently — two
    renderings of one answer, kept in step by hand. That is the shape that printed a
    coverage gap to humans only, invisible to the agent reading JSON that most needed
    it. Now both derive from one `Outcome`, and this checks they have not drifted apart
    again.
    """
    import json as _json

    from ddflow.surfaces.mcp import Server

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "A", "--needs", "B")
    run_cli(repo, "task", "add", "B", "--needs", "A")  # a real cycle, so findings exist

    _code, cli_out, _err = run_cli(repo, "--json", "loops")
    from_cli = _json.loads(cli_out)
    assert from_cli, "no findings at all; this test would prove nothing"

    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_loops", "arguments": {}},
        }
    )
    text = reply["result"]["content"][0]["text"]
    # Parsed WITHOUT reaching into a key. The first version indexed `["findings"]`, which
    # validated the contents while missing that the top-level SHAPE had changed from an
    # array to an object -- the exact break it was written to prevent, hidden by the
    # test's own accommodation of it.
    start = min((i for i in (text.find("["), text.find("{")) if i != -1), default=-1)
    from_mcp = _json.loads(text[start:])
    assert isinstance(from_mcp, list), f"the MCP body is no longer an array: {text[:200]}"
    assert from_mcp == from_cli, (
        f"the two surfaces disagree about the same answer:\nCLI: {from_cli}\nMCP: {from_mcp}"
    )


def test_a_migrated_tool_keeps_its_exit_contract(repo):
    """Migration must not silently renumber exit codes. `loops` returns 1 when there
    are findings and 2 when there are none — callers branch on that, and "loops found"
    being a 1 rather than a 0 is a contract this layer inherits rather than redesigns.
    """
    run_cli(repo, "init")
    assert run_cli(repo, "loops")[0] == NOTHING

    run_cli(repo, "task", "add", "A", "--needs", "B")
    run_cli(repo, "task", "add", "B", "--needs", "A")
    assert run_cli(repo, "loops")[0] == FAIL


# -- the write tools, which the shape comparison cannot reach ---------------------------


def test_recording_a_decision_over_mcp_records_it(repo):
    """`ddflow_decision_add` on the typed path, end to end.

    It is exempt from `MIGRATED_WIRE_SHAPES` because running it twice records two
    decisions with different ids — so the contract is checked by asserting what it DID,
    which is the thing a consumer actually depends on.
    """
    import json as _json

    from ddflow.surfaces.mcp import Server

    run_cli(repo, "init")
    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "ddflow_decision_add",
                "arguments": {
                    "title": "Use pluggy",
                    "decision": "every stage is a Step",
                    "globs": "ddflow/steps/*",
                },
            },
        }
    )
    assert reply["result"]["isError"] is False, reply
    text = reply["result"]["content"][0]["text"]
    body = _json.loads(text[text.index("{") :])
    assert set(body) == {"id"}, f"the wire body was {body}, not {{'id': ...}}"

    _code, out, _ = run_cli(repo, "--json", "decision", "list")
    rows = _json.loads(out)
    assert [r["id"] for r in rows] == [body["id"]], "the decision was not recorded"
    assert rows[0]["globs"] == ["ddflow/steps/*"], "globs were dropped on the way through"


def test_a_decision_with_no_globs_warns_on_BOTH_surfaces(repo):
    """The bug class this layer exists for.

    A decision with no globs cannot be surfaced automatically to an agent working the
    code it governs — it will only ever be found by someone already looking. That
    warning was computed inside the human branch of `_decision_add`, so the agent
    driving over MCP, which is always JSON, was never told. It is now a field on the
    Outcome and both surfaces read it.
    """
    from ddflow import api

    run_cli(repo, "init")
    without = api.decision_add(repo, api.decisions.Draft(title="t", decision="d"))
    with_globs = api.decision_add(repo, api.decisions.Draft(title="t", decision="d", globs="x.py"))
    assert without.data["ungoverned"] is True
    assert with_globs.data["ungoverned"] is False


def test_superseding_over_mcp_refuses_without_a_replacement(repo):
    """A decision is never simply deleted. The refusal is the behaviour."""
    from ddflow import api

    run_cli(repo, "init")
    run_cli(repo, "decision", "add", "--id", "D1", "--title", "t", "--decision", "d")
    assert api.decision_supersede(repo, "D1", by="").exit == FAIL
    assert api.decision_supersede(repo, "NOPE", by="D2").exit == FAIL
    assert api.decision_supersede(repo, "D1", by="D2").exit == OK


def test_a_decision_must_say_what_was_DECIDED(repo):
    """The refusal that gives the record its value, probed for the first time.

    `_decision_add` has always refused a record with no `--decision`: notes about what
    was *discussed* are indistinguishable from a decision, and the next agent cannot
    act on them. Nothing tested it — mutating the guard to `if False and ...` left the
    entire decisions suite green, which is how a deliberate refusal quietly becomes
    optional. Found by mutation-testing the B37 migration of this family.
    """
    from ddflow import api

    run_cli(repo, "init")
    refused = api.decision_add(repo, api.decisions.Draft(title="we talked about caching"))
    assert refused.exit == FAIL, refused
    assert "what was DECIDED" in refused.reason

    _code, out, _ = run_cli(repo, "--json", "decision", "list")
    assert json.loads(out) == [], "a refused decision was recorded anyway"

    accepted = api.decision_add(
        repo, api.decisions.Draft(title="caching", decision="cache on the fingerprint")
    )
    assert accepted.exit == OK, accepted


def test_superseded_decisions_are_hidden_but_COUNTED(repo):
    """ "3 superseded, --all to include them" needs the number to be real.

    `decision list` shows only what is in force, which is right — but a reader who is
    not told that anything was hidden cannot know to ask. The count is on the Outcome so
    both surfaces have it; mutating it to a constant `0` also left the suite green.
    """
    from ddflow import api

    run_cli(repo, "init")
    run_cli(repo, "decision", "add", "--id", "D1", "--title", "old", "--decision", "a")
    run_cli(repo, "decision", "add", "--id", "D2", "--title", "new", "--decision", "b")
    run_cli(repo, "decision", "supersede", "D1", "--by", "D2")

    live = api.decision_list(repo)
    assert [r["id"] for r in live.data["rows"]] == ["D2"]
    assert live.data["hidden"] == 1, "the superseded decision was hidden AND uncounted"

    every = api.decision_list(repo, all=True)
    assert {r["id"] for r in every.data["rows"]} == {"D1", "D2"}
    assert every.data["hidden"] == 0, "--all hides nothing, so it must count nothing"


# -- the projection must survive every exit code ----------------------------------------


def test_a_projection_has_the_same_SHAPE_whatever_the_outcome():
    """`Outcome.body(("a", "b"))` on an outcome that only carries `a`.

    This indexed the keys, so an operation with less to say produced a KeyError inside
    the MCP dispatcher and the caller got a crash where the point was to deliver the
    refusal. `workflow drop` on a gate in no pipeline is exactly that outcome.

    A consumer branches on the shape; an explicit `null` is branchable and a missing key
    is not. Probed at the unit level because the two guards for it — this, and every api
    function carrying its projected keys on every path — are redundant by design, so
    neither is observable through the other.
    """
    partial = O.nothing("k", "nothing to do", gate="g")
    assert partial.body(("gate", "removed_from", "applied")) == {
        "gate": "g",
        "removed_from": None,
        "applied": None,
    }
    assert O.failed("k", "broke").body(("a",)) == {"a": None}
    # A single-key payload still indexes: that key IS the body, and inventing a `null`
    # body would turn "the operation has no rows" into "the operation returned nothing".
    assert O.ok("k", rows=[]).body("rows") == []


def test_setting_a_pipeline_names_the_undefined_gate_and_guesses_the_intent(repo):
    """Refused BEFORE the write, with the near-miss named.

    `services/configwrite` also refuses an incoherent pipeline, so this check is
    defence-in-depth — which is why it needs its own probe: disabling it left every test
    green because the second guard caught the write anyway, with a worse message. The
    message is the point. An unknown id in a pipeline blocks every item that enters it
    forever, and "did you mean 'unit_tests'?" is the difference between a typo found now
    and a queue that stops moving tomorrow.
    """
    from ddflow import api

    run_cli(repo, "init")
    out = api.workflow_pipeline(repo, "task", "unit_testz", dry_run=True)
    assert out.exit == FAIL, out
    assert out.data["unknown"] == ["unit_testz"], out.data
    assert "did you mean 'unit_tests'?" in out.reason, out.reason
    assert out.data["applied"] is False


def test_an_incoherent_workflow_is_REPORTED_as_incoherent(repo):
    """`coherent` is the one field a caller uses to decide whether to trust the rest.

    Written straight into `config.toml` rather than through the writer, because the
    writer refuses incoherent edits — which means the only way a project GETS an
    incoherent workflow is by editing the file, and so the only way to test the report
    is the same way.
    """
    from ddflow import api

    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text() + '\n[gates]\ntask_pipeline = ["ghost_gate"]\n')

    out = api.workflow_show(repo)
    assert out.exit == FAIL, "an incoherent workflow reported success"
    assert out.data["coherent"] is False, out.data
    assert out.data["findings"], "no finding explains what is wrong"


def test_render_only_data_never_reaches_the_wire():
    """`_`-prefixed keys are for the local prose renderer.

    The alternative to carrying them is folding the log a second time in the surface,
    which is precisely the duplication this layer removes — `cmd_status` needs the
    completed-task OBJECTS to sort by `completed_at`, not a list of dicts. Carrying them
    without a rule would serialise live dataclasses to every MCP caller as repr strings.
    """
    out = O.ok("status", tasks={"total": 3}, _render={"objects": object()})
    assert out.body() == {"tasks": {"total": 3}}, out.body()
    assert "_render" in out.data, "the renderer still needs it"


# -- correctness, which a parity test cannot reach ---------------------------------------
#
# `test_a_migrated_tool_reproduces_its_CLI_json_exactly` compares the two surfaces, and
# after migration BOTH read the same `api` function — so a wrong answer is identically
# wrong on both sides and the comparison passes. Mutation testing made this concrete:
# forcing `status`'s completed count to 0, dropping `show`'s path resolution and
# reporting an empty `recover` as success all left the whole suite green.
#
# The rule that follows: every migrated operation needs a wire-shape row AND at least one
# probe of what it actually answers. The first stops a refactor changing the contract; only
# the second stops it changing the truth.


def test_status_counts_what_actually_happened(repo):
    """The numbers, not their agreement across surfaces."""
    from ddflow import api

    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "P")
    run_cli(repo, "task", "add", "T1", "--phase", "P1", "--globs", "a.py")
    run_cli(repo, "task", "add", "T2", "--phase", "P1", "--globs", "b.py")

    before = api.status(repo)
    assert before.data["tasks"] == {
        "total": 2,
        "done": 0,
        "running": 0,
        "ready": 2,
        "blocked": 0,
    }, before.data["tasks"]

    run_cli(repo, "claim", "T1")
    claimed = api.status(repo)
    assert claimed.data["tasks"]["running"] == 1, claimed.data["tasks"]
    assert [x["id"] for x in claimed.data["in_flight"]] == ["T1"], claimed.data["in_flight"]

    run_cli(repo, "abandon", "T1", "--reason", "enough")
    after = api.status(repo)
    assert after.data["tasks"]["running"] == 0, after.data["tasks"]


def test_status_counts_a_completed_task_as_done(repo):
    """Separate from the above because completion needs the gates satisfied, and the
    count being right is the single number a reader trusts most."""
    from ddflow import api

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    run_cli(repo, "claim", "T1")
    # `--force`, deliberately. Completion has a rule-set of its own — required gates,
    # evidence, reviewer independence — and `tests/test_completion_policy.py` is where
    # that belongs. This probe asks only whether `status` COUNTS a completed task, so it
    # takes the documented override rather than reproducing ten gate recordings whose
    # requirements would then be pinned here by accident.
    code, _out, err = run_cli(repo, "complete", "T1", "--force")
    assert code == OK, err

    out = api.status(repo)
    assert out.data["tasks"]["done"] == 1, out.data["tasks"]
    assert [t["id"] for t in out.data["completed_tasks"]] == ["T1"], out.data["completed_tasks"]


def test_show_returns_a_worktree_path_the_caller_can_use(repo):
    """Storage portable, interface usable.

    The log stores worktree paths RELATIVE to the repo root, which is what makes a
    committed log true on every checkout. A caller handed ".ddflow-worktrees/T1" has to
    know what it is relative to, and resolves it against its own cwd — which for an agent
    is frequently not the repo root.
    """
    import os

    from ddflow import api

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    run_cli(repo, "claim", "T1")

    wt = api.show(repo, "T1").data["item"]["worktree"]
    assert wt, "the claimed item has no worktree at all"
    assert os.path.isabs(wt), f"{wt!r} is relative; a caller cannot cd to it"


def test_nothing_to_recover_is_exit_2_not_success(repo):
    """ "No data" is reported as itself. A clean repo and a repo whose sweep failed must
    not look the same to a caller that only reads the exit code."""
    from ddflow import api

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    out = api.recover(repo)
    assert out.exit == NOTHING, out
    assert out.data["count"] == 0
    assert "Nothing to recover" in out.reason
