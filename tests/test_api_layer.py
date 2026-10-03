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
ARGV_TOOLS_CEILING = 0


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
    run_cli(repo, "task", "add", "A", "--globs", "a.py")
    run_cli(repo, "task", "add", "B", "--globs", "b.py", "--needs", "A")

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
    result = api.update(repo, "T1", api.ItemEdit())
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
    "ddflow_workflow_state": (["workflow", "state"], {}),
    "ddflow_bug_file_tasks": (["bug", "file-tasks"], {}),
    "ddflow_status": (["status"], {}),
    "ddflow_export": (["export"], {}),
    "ddflow_rebuild": (["rebuild"], {}),
    "ddflow_show": (["show", "T1"], {"id": "T1"}),
    "ddflow_recover": (["recover"], {}),
    # Text-bodied: their CLI form has no `--json` either, so the comparison is
    # document against document.
    "ddflow_doctor": (["doctor"], {}),
    "ddflow_board": (["board"], {}),
    "ddflow_replay": (["replay"], {}),
    "ddflow_render": (["render", "--show", "board"], {"show": "board"}),
    "ddflow_gate_status": (["gate", "status", "T1"], {"id": "T1"}),
    "ddflow_next": (["next"], {}),
    # timeout 0: the question without the sleep, so both surfaces answer the same moment.
    "ddflow_wait": (["wait", "--timeout", "0"], {"timeout": 0}),
    "ddflow_brief": (["brief"], {}),
    "ddflow_history": (["history"], {}),
    # limit 50: the CLI default; over MCP the default is the bounded 25.
    "ddflow_list": (["task", "list"], {"kind": "task", "limit": 50}),
    "ddflow_lesson_search": (["lesson", "search", "x"], {"query": "x"}),
    "ddflow_lesson_verify": (["lesson", "verify"], {}),
    "ddflow_memory_list": (["memory", "list"], {}),
    "ddflow_job_list": (["job", "list"], {}),
    "ddflow_recall": (["recall", "x"], {"query": "x"}),
    "ddflow_similar": (["similar", "x"], {"text": "x"}),
    "ddflow_reviewers_list": (["reviewers", "list"], {}),
    "ddflow_cleanup": (["cleanup"], {}),
    "ddflow_cadence": (["cadence"], {}),
    # The fixture repository has no suite, so this is the exit-2 body; the OK body is compared
    # against the CLI's in tests/test_prose_pins.py.
    "ddflow_pins": (["pins", "README.md"], {"document": "README.md"}),
    # The fixture's only change is uncommitted setup, so this compares the no-test body.
    "ddflow_tests": (["tests"], {}),
    # Without `write` it only proposes, so both surfaces see the same repository.
    "ddflow_precommit": (["precommit"], {}),
    "ddflow_import_verify": (["import", "--verify"], {}),
    "ddflow_companions": (["companions", "list"], {}),
    "ddflow_hooks": (["hooks", "status"], {}),
    "ddflow_help": (["help"], {}),
    "ddflow_prompts": (["prompts", "list"], {}),
    "ddflow_configure": (["config", "--explain"], {}),
    "ddflow_gate_verify": (
        ["gate", "verify", "T1", "unit_tests"],
        {"id": "T1", "gate": "unit_tests"},
    ),
    "ddflow_pr_status": (["pr", "status"], {}),
    "ddflow_pr_sync": (["pr", "sync"], {}),
    "ddflow_version_show": (["version", "show"], {}),
    # --dry-run writes nothing, so both surfaces see the same state.
    "ddflow_version_cut": (["version", "cut", "--dry-run"], {"dry_run": True}),
    "ddflow_flow_show": (["flow", "show"], {}),
    "ddflow_promote_status": (["promote", "status"], {}),
    "ddflow_rule_list": (["rule", "list"], {}),
    "ddflow_rule_search": (["rule", "search", "test"], {"query": "test"}),
    "ddflow_rule_show": (["rule", "show", "r-test"], {"id": "r-test"}),
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
#: Migrated tools whose body is a DOCUMENT rather than JSON, on both surfaces and before
#: the migration too — their argv form carries no `--json`. Compared text-to-text; parsing
#: them as JSON is what the first version of the comparison did, and it reported a
#: markdown board as "no JSON body".
TEXT_BODIED = {
    "ddflow_brief",
    # The sites a forbidden pattern reappeared at ARE the finding (B20): a caller reads
    # which, rather than parsing how many.
    "ddflow_lesson_verify",
    "ddflow_configure",
    "ddflow_reviewers_list",
    "ddflow_doctor",
    "ddflow_gate_status",
    "ddflow_board",
    "ddflow_replay",
    "ddflow_render",
}

WRITES_NOT_COMPARABLE = {
    "ddflow_promote_add",
    "ddflow_flow_choose",
    "ddflow_update",
    # Releases held work: a second call finds nothing blocked and exits 2. Its behaviour
    # is pinned by tests/test_import_fidelity.py.
    "ddflow_unblock",
    # Each call records a new memory under a content-addressed id / forgets one.
    "ddflow_memory_add",
    "ddflow_memory_forget",
    # Records an observation the FIRST time it sees a change: the second surface sees
    # none. Its row compared two empty results (roborev 829); pinned by
    # tests/test_external_deps.py instead.
    "ddflow_external_sync",
    # Launch a process / register one / end one: none can be invoked twice identically.
    "ddflow_job_run",
    "ddflow_job_add",
    "ddflow_job_end",
    "ddflow_setup",
    "ddflow_workflow_pipeline",
    "ddflow_workflow_gate",
    "ddflow_workflow_drop",
    "ddflow_decision_add",
    "ddflow_decision_supersede",
    "ddflow_gate_run",
    "ddflow_gate_record",
    "ddflow_gate_skip",
    "ddflow_phase_add",
    "ddflow_task_add",
    "ddflow_split",
    # Settles a contest: a second call finds nothing contested and is refused (exit 3).
    "ddflow_resolve",
    "ddflow_claim",
    "ddflow_heartbeat",
    "ddflow_release",
    "ddflow_complete",
    "ddflow_abandon",
    "ddflow_remove",
    "ddflow_block",
    "ddflow_merge",
    "ddflow_lesson_add",
    "ddflow_research_add",
    "ddflow_bug_found",
    "ddflow_bug_fixed",
    "ddflow_bug_invalid",
    "ddflow_session_start",
    "ddflow_session_prompt",
    "ddflow_session_note",
    "ddflow_session_end",
    "ddflow_review",
    "ddflow_review_triage",
    "ddflow_reviewers_detect",
    "ddflow_import",
    "ddflow_companions_add",
    "ddflow_rule_add",
    "ddflow_rule_edit",
    "ddflow_rule_remove",
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
    # A rule with an explicit id, so the rule tools have a row to compare, not two empties.
    run_cli(
        repo, "rule", "add", "--id", "r-test", "--title", "Test rule", "--content", "test content"
    )
    # A fixed probe result for every companion, so neither surface shells out to detect
    # one. Each probe runs the real tool (npx, docker, ...) with a timeout, and two live
    # probes need not agree: under load one times out (`unknown`) while the other
    # answers (`installed`), and the comparison fails on the machine, not on a surface.
    # `flipflop` makes that disagreement certain instead of load-dependent. Its probe
    # cannot be spawned during the CLI call (no interpreter line: exec format error, the
    # same "could not tell" a timeout gives, and likewise never cached) and answers
    # during the MCP call, so a test that lets the surfaces probe live fails every run.
    from ddflow.services import companions

    probe = repo / "flipflop-probe"
    probe.write_text("not a program\n", "utf-8")
    probe.chmod(0o755)
    (repo / ".ddflow" / "companions.toml").write_text(
        f'[[companion]]\nid = "flipflop"\nkind = "cli"\ndetect = ["{probe}"]\n', "utf-8"
    )
    companions._write_cache(
        repo, {c.id: (c.id != "optmem", f"fixed for {c.id}") for c in companions.load(repo)}
    )

    argv, arguments = MIGRATED_WIRE_SHAPES[tool]
    # `--json` is passed for the JSON tools only. A text-bodied tool ignores it today,
    # but passing it would encode "these are the same kind of thing" into the check that
    # exists to tell them apart.
    head = [] if tool in TEXT_BODIED else ["--json"]
    _code, cli_out, _err = run_cli(repo, *head, *argv)
    from_cli = None if tool in TEXT_BODIED else _json.loads(cli_out)
    probe.write_text("#!/bin/sh\nexit 0\n", "utf-8")  # flipflop now answers: see above

    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
    )
    text = reply["result"]["content"][0]["text"]

    if tool in TEXT_BODIED:
        # Document against document. `run_cli` strips nothing, so the only permitted
        # difference is the trailing newline `print` adds.
        assert text.rstrip("\n") == cli_out.rstrip("\n"), (
            f"{tool}: the two surfaces render different documents\n"
            f"CLI: {cli_out[:300]!r}\nMCP: {text[:300]!r}"
        )
        assert text.strip(), f"{tool} returned an empty document"
        return

    from_mcp_text = text
    start = min((i for i in (text.find("["), text.find("{")) if i != -1), default=-1)
    assert start != -1, f"{tool} returned no JSON body: {from_mcp_text[:200]}"
    from_mcp = _json.loads(text[start:])

    assert type(from_mcp) is type(from_cli), (
        f"{tool}: the MCP body is a {type(from_mcp).__name__} and the CLI's is a "
        f"{type(from_cli).__name__} — a migration changed the wire shape"
    )
    # The five bounded reads (B-mcp-payload-bound) are the deliberate exception: the MCP
    # body is the CLI's body put through `mcp_bound`, and nothing else. Their cuts are
    # pinned by tests/test_mcp_payload_bound.py.
    from ddflow.surfaces.mcp_bound import BOUNDS

    if tool in BOUNDS:
        from_cli = BOUNDS[tool](from_cli, arguments)[0]
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
        "held_by_cap": 0,
        "blocked": 0,
        "review": 0,
        "abandoned": 0,
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


def test_every_text_bodied_tool_actually_returns_a_STRING(repo):
    """A text tool whose payload names a non-string is a TypeError on a live connection.

    This replaced a check that every text-bodied tool's kind had a renderer in
    `views/human.py` — which was the wrong invariant: `board` and `render` produce their
    documents in `views/markdown.py` and `replay` in `services/sessions.py`, all of which
    are already below both surfaces. WHERE the text is rendered does not matter; that the
    declared payload holds a rendered string does.
    """
    from ddflow.surfaces.mcp import TOOLS, Server

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    srv = Server(repo)

    checked = 0
    for name in sorted(TEXT_BODIED):
        spec = TOOLS[name]
        assert "api" in spec, f"{name} is listed as text-bodied but is not migrated"
        _argv, arguments = MIGRATED_WIRE_SHAPES[name]
        reply = srv.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        body = reply["result"]["content"][0]["text"]
        assert body.strip(), f"{name} returned an empty document"
        # Not JSON. A tool that silently started returning `{"text": "..."}` would still
        # be a string here, and this is the assertion that says which one it is.
        assert not body.lstrip().startswith(("{", "[")), (
            f"{name} declares a text body but returned what looks like JSON: {body[:120]!r}"
        )
        checked += 1
    assert checked == len(TEXT_BODIED)


def test_a_document_body_is_not_prefixed_with_the_reason():
    """`doctor` ends with "2 problem(s)."; leading with "2 problem(s)" says it twice.

    The string path returned the report ALONE, so prefixing is also a wire change. A
    document's renderer decides its own lead — that is what distinguishes it from a JSON
    body, where the reason leading the text is exactly what a reader wants first.
    """
    from ddflow.surfaces.mcp import _outcome_result

    failed = O.failed("doctor", "2 problem(s)", text="the report\n2 problem(s).")
    body = _outcome_result(failed, "text", as_text=True)["content"][0]["text"]
    assert body == "the report\n2 problem(s).", body
    assert body.count("2 problem(s)") == 1, body

    # A JSON body stays parseable at `content[0]`; its reason is the second block.
    failed_json = _outcome_result(O.failed("k", "broke", rows=[]), "rows")
    assert failed_json["content"][0]["text"] == "[]", failed_json["content"]
    assert failed_json["content"][1]["text"] == "broke", failed_json["content"]


def test_an_EMPTY_document_falls_back_to_the_reason():
    """Returning nothing for a failed call is the unavailable-as-success class with no
    text to hide behind: the caller sees a blank body and an exit code it may not read."""
    from ddflow.surfaces.mcp import _outcome_result

    out = _outcome_result(O.failed("render", "unknown view 'nope'", text=""), "text", as_text=True)
    assert out["content"][0]["text"] == "unknown view 'nope'"
    assert out["isError"] is True


def test_a_text_tool_whose_payload_is_not_a_string_fails_loudly():
    """A misdeclared payload would otherwise ship a `repr` of a list to the client as if
    it were prose."""
    from ddflow.surfaces.mcp import _outcome_result

    with pytest.raises(TypeError, match="declared `text`"):
        _outcome_result(O.ok("board", text=["not", "a", "string"]), "text", as_text=True)


def test_doctor_calls_a_broken_workflow_a_PROBLEM_not_a_note(repo):
    """The problem/note split IS doctor's value, and it decides the exit code.

    A pipeline naming a gate that has no definition is the one config error that is both
    silent and permanent: every item entering the pipeline blocks on an outcome that can
    never be recorded. Filed as a note it becomes exit 0 — advice, in a report an operator
    runs precisely to be told whether anything is wrong. Mutating the classification to
    "always a note" left the whole suite green.
    """
    from ddflow import api

    run_cli(repo, "init")
    healthy = api.doctor(repo)
    assert healthy.exit == OK, f"a fresh project is not healthy: {healthy.data['problems']}"

    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text() + '\n[gates]\ntask_pipeline = ["ghost_gate"]\n')

    out = api.doctor(repo)
    assert out.exit == FAIL, "an undefined gate in the pipeline reported as healthy"
    assert any("ghost_gate" in p for p in out.data["problems"]), out.data
    assert not any("ghost_gate" in n for n in out.data["notes"]), (
        f"filed as a note, so `doctor` exits 0: {out.data['notes']}"
    )
    assert "PROBLEM:" in out.data["text"], out.data["text"][:300]


def test_a_wire_field_that_shadows_the_Outcome_is_rejected(repo):
    """`gate verify` and `decision supersede` both carry a `reason` ON THE WIRE, meaning
    something different from the Outcome's `reason`. Spreading `**data` into a helper
    conflates them.

    The two are caught in different places and this asserts both, because the messages
    differ and the first one is the confusing one: `failed()` declares `reason` as a
    parameter so Python rejects the duplicate before any check of ours can run, while
    `ok()` has no such parameter and would otherwise swallow `reason=` into `data`
    silently — which is the worse failure, since nothing would raise at all.
    """
    with pytest.raises(TypeError, match="multiple values for argument 'reason'"):
        O.failed("k", "the outcome's reason", reason="the wire's reason")

    with pytest.raises(TypeError, match="cannot take"):
        O.ok("k", reason="the wire's reason")
    with pytest.raises(TypeError, match="Build it directly"):
        O.ok("k", exit=0)

    # The documented escape hatch, which both real cases now use.
    out = O.Outcome(kind="k", data={"reason": "on the wire"}, exit=O.FAIL, reason="the outcome's")
    assert out.data["reason"] == "on the wire"
    assert out.reason == "the outcome's"
    assert out.body() == {"reason": "on the wire"}


# -- the gate pipeline's policy, which nothing probed -------------------------------------


def _pipeline(repo):
    from ddflow import api

    return api.workflow_show(repo).data["task_pipeline"]


def test_enforce_order_block_actually_blocks(repo):
    """The order in the pipeline is not decoration.

    A rubber-duck review recorded before `implement` reviewed an empty diff; a `merge`
    recorded before `unit_tests` merged something nobody tested. `block` is the policy
    that says so — and disabling it left the entire suite green, so for as long as it has
    existed nothing has demonstrated that it refuses anything.
    """
    from ddflow import api

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    pipeline = _pipeline(repo)
    assert len(pipeline) > 2, pipeline
    later = pipeline[-2]

    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text() + '\n[gates]\nenforce_order = "block"\n')

    out = api.gate_record(repo, "T1", later, outcome="passed", evidence=api.GateEvidence(note="x"))
    assert out.exit == REFUSED, out
    assert pipeline[0] in out.reason, out.reason
    assert out.data["ahead"], out.data

    _code, shown, _ = run_cli(repo, "--json", "show", "T1")
    assert not json.loads(shown)["gates"].get(later), "refused and recorded anyway"


def test_recording_out_of_order_under_warn_records_the_violation(repo):
    """ "Whether `warn` should become `block` is a judgement about how often this fires,
    and for as long as it only ever printed, that judgement had no evidence behind it."

    That is the comment on the event. Nothing checked the event was written, so the
    evidence it exists to gather was never actually gathered.
    """
    from ddflow import api

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    pipeline = _pipeline(repo)
    later = pipeline[-2]

    out = api.gate_record(repo, "T1", later, outcome="passed", evidence=api.GateEvidence(note="x"))
    assert out.exit == OK, out  # `warn` is the default: recorded, not refused

    shard = next((repo / ".ddflow" / "events").glob("*.jsonl"))
    kinds = [json.loads(ln)["kind"] for ln in shard.read_text().splitlines() if ln.strip()]
    assert "gate.out_of_order" in kinds, f"the violation was not recorded: {sorted(set(kinds))}"


def test_running_an_AGENT_gate_refuses_and_hands_over_the_instruction(repo):
    """ddflow cannot perform an agent gate, and must not pretend to have tried.

    Exit 2 with the gate's prompt — not a command gate's empty run, which would record an
    outcome for work nobody did.
    """
    from ddflow import api

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    pipeline = _pipeline(repo)
    agent_gate = next(
        g
        for g in pipeline
        if not api.workflow_show(repo).data["gates"][pipeline.index(g)]["command"]
    )

    out = api.gate_run(repo, "T1", agent_gate)
    assert out.exit == NOTHING, out
    assert "AGENT gate" in out.reason, out.reason
    assert "gate record" in out.reason, "it does not say how to record the result"
    assert out.data["outcome"] == "", out.data

    _code, shown, _ = run_cli(repo, "--json", "show", "T1")
    assert not json.loads(shown)["gates"].get(agent_gate), "it recorded an outcome anyway"


def test_verification_with_no_results_is_never_a_pass():
    """`all([])` is True, and that is the whole hazard.

    `results` is empty on every pre-flight failure — unknown gate, agent gate, no
    registered mutations, no green baseline — so scoring on `results` alone turns each of
    those into "this gate can fail". Asserted on the SCORING RULE rather than end to end,
    because every reachable pre-flight failure also sets `reason`, which means the
    `bool(results)` clause is defence in depth and cannot be reached through the CLI. A
    guard that cannot be reached is exactly the kind that gets deleted as redundant.
    """
    from ddflow.api import gates as G

    def verified(results, reason):
        return bool(results) and not reason and all(r for r in results)

    assert verified([], "") is False, "an empty verification scored as a pass"
    assert verified([True], "") is True
    assert verified([True], "no green baseline") is False
    assert verified([False], "") is False
    # And the rule under test is the one the module actually uses.
    src = (Path(G.__file__)).read_text()
    assert '"verified": bool(results) and not reason and all(r.ok for r in results)' in src, (
        "the scoring rule changed; this test is now checking a copy of it"
    )


def test_one_split_cannot_name_the_same_child_twice(repo):
    """The bug this guard was written for, finally probed.

    Its comment describes what happened: `--into X=one --into X=two` appended two
    `task.added` events for one id, `fold` MERGED them, and the split reported two
    children while producing one whose title was silently the second spec's. The guard
    was added; nothing ever checked it, so disabling it left the suite green.

    Also asserts the refusal is TOTAL. The same comment records why: validating and
    appending in one pass meant a collision on the second `--into` exited non-zero having
    already written the first, leaving the parent an umbrella nobody asked for —
    un-claimable because it now had a child, un-completable because that child was open.
    """
    from ddflow import api

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py", "--title", "two things")

    out = api.split(repo, "T1", into=["T1.a=one", "T1.a=two"])
    assert out.exit == FAIL, out
    assert "given twice" in out.reason, out.reason
    assert out.data["created"] == [], out.data

    _code, shown, _ = run_cli(repo, "--json", "show", "T1")
    body = json.loads(shown)
    assert "Split into" not in (body["body"] or ""), "a refused split recorded itself"
    _code, board, _ = run_cli(repo, "--json", "progress")
    assert "T1.a" not in board, "a refused split created a child anyway"


def test_a_task_cannot_be_added_under_a_parent_that_does_not_exist(repo):
    """A dangling parent is a queue that misrepresents the project.

    The child is unreachable from any phase, `plan()` cannot order it against work it
    should follow, and the only thing that notices is `doctor` — after the fact.
    """
    from ddflow import api

    run_cli(repo, "init")
    out = api.task_add(repo, "T1", title="orphan", parent="P-NOT-REAL")
    assert out.exit == FAIL, out
    assert "no such parent" in out.reason, out.reason

    _code, rows, _ = run_cli(repo, "--json", "progress")
    assert "T1" not in rows, "the task was added under a parent that does not exist"


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
    (("task", "add"), "priority", "DEFAULT_PRIORITY"),
    (("brief",), "check_recovery", "DEFAULT_CHECK_RECOVERY"),
    (("render",), "out", "DEFAULT_RENDER_DIR"),
]


@pytest.mark.parametrize(("path", "dest", "constant"), DEFAULT_SOURCES)
def test_the_parser_and_the_api_agree_on_every_default(path, dest, constant):
    import argparse as _argparse

    from ddflow import api
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
    """A constant the functions do not use is documentation, not a default."""
    import inspect

    from ddflow import api

    pairs = [
        (api.next_item, "kind", api.DEFAULT_NEXT_KIND),
        (api.phase_add, "priority", api.DEFAULT_PRIORITY),
        (api.task_add, "priority", api.DEFAULT_PRIORITY),
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
    from ddflow import api

    run_cli(repo, "init")

    # `--kind` -> "task". A phase is an umbrella; "work on P1" is not an instruction.
    run_cli(repo, "phase", "add", "P1", "--title", "P")
    run_cli(repo, "task", "add", "T1", "--phase", "P1", "--globs", "a.py")
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
    from ddflow import api

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
    from ddflow import api

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
    from ddflow import api

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
    from ddflow import api
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
    from ddflow import api

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


# -- an unavailable reviewer is never a passed review --------------------------------------


def _gate_outcome(repo, item, gate):
    """The recorded OUTCOME string. `gates[<id>]` is the whole record — outcome, when, by
    whom — and comparing the dict to a string passes an assertion that reads correctly."""
    _code, shown, _ = run_cli(repo, "--json", "show", item)
    return (json.loads(shown)["gates"].get(gate) or {}).get("outcome", "")


def test_no_reviewer_configured_records_UNAVAILABLE_not_a_pass(repo):
    """The vacuous-pass class at the level of a whole reviewer.

    A project with no reviewer must not look like a project whose reviewer approved. And
    the outcome has to be RECORDED: an unrecorded UNAVAILABLE is indistinguishable from a
    gate nobody ran, which is how "we reviewed it" becomes true on paper.
    """
    from ddflow import api

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")

    out = api.run_review(repo, gate="critic", item="T1", intent="add a guard")
    assert out.exit == NOTHING, out
    assert out.data["outcome"] == "unavailable", out.data
    assert "NOT a pass" in out.reason, out.reason
    assert _gate_outcome(repo, "T1", "critic") == "unavailable", (
        "the gate was left with no outcome at all, which reads as 'not run yet'"
    )


def _stub_reviewer(repo):
    """A reviewer pointing at a closed port. Unreachable is a case, not a broken test."""
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(
        cfg.read_text() + '\n[[reviewer]]\nname = "stub"\nbase_url = "http://127.0.0.1:1"\n'
        'model = "stub/model"\nfamily = "stub"\ngates = ["critic"]\n'
    )


def test_an_unreachable_reviewer_records_UNAVAILABLE_not_a_pass(repo):
    """The commonest way a review silently does not happen.

    A configured endpoint that does not answer is exactly the case that looks like
    success from a distance: the gate ran, nothing was reported, nothing failed. Exit 2
    and a recorded `unavailable` is what keeps it distinguishable from approval.
    """
    from ddflow import api

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    _stub_reviewer(repo)
    (repo / "a.py").write_text("x = 1\n")  # something to review

    out = api.run_review(repo, gate="critic", item="T1", intent="add a guard")
    assert out.exit == NOTHING, out
    assert out.data["outcome"] == "unavailable", out.data
    assert _gate_outcome(repo, "T1", "critic") == "unavailable"


def test_an_empty_diff_records_UNAVAILABLE_not_a_pass(repo):
    """A reviewer handed nothing reports nothing, and reporting nothing is not approval.

    The shape the whole review stack is built against: the model dutifully reviews an
    empty diff and returns no findings, which every downstream reader treats as a clean
    bill of health.
    """
    import subprocess

    from ddflow import api

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    _stub_reviewer(repo)
    # Commit EVERYTHING, including what `init` wrote, so the diff is genuinely empty and
    # the run stops before it ever reaches the endpoint.
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "all"], check=True)

    out = api.run_review(repo, gate="critic", item="T1", intent="add a guard")
    assert out.exit == NOTHING, out
    assert out.data["outcome"] == "unavailable", out.data
    assert "Empty diff" in out.reason, out.reason
    assert _gate_outcome(repo, "T1", "critic") == "unavailable"


def test_review_without_an_intent_is_refused(repo):
    """ "The reviewer flags where the diff and the stated intent disagree, so without it
    there is nothing to disagree with."

    Exit 1, not 2: this is a caller error with a remedy, not an unavailable resource.
    """
    from ddflow import api

    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(
        cfg.read_text() + '\n[[reviewer]]\nname = "stub"\nbase_url = "http://127.0.0.1:1"\n'
        'model = "stub/model"\nfamily = "stub"\ngates = ["critic"]\n'
    )
    (repo / "a.py").write_text("x = 1\n")

    out = api.run_review(repo, gate="critic", intent="")
    assert out.exit == FAIL, out
    assert "--intent is required" in out.reason


def test_no_reviewer_status_maps_to_a_PASS_except_a_real_review():
    """The outcome table, asserted as a table.

    `R.ERROR` — the reviewer raised — is a separate status from `R.UNAVAILABLE`, and
    mapping it to `"passed"` left every test green: the unreachable-endpoint case goes
    through UNAVAILABLE, so nothing reached the ERROR arm. Inducing a genuine ERROR means
    breaking the review service from a test, which would pin its internals; asserting the
    MAPPING is the honest alternative, and it is the thing that must not drift.

    Only REVIEWED may produce `passed`, and only when it found nothing.
    """
    import inspect

    from ddflow.api import review as A
    from ddflow.services import review as R

    src = inspect.getsource(A.review)
    table = src[src.index("outcome = {") : src.index("}[best.status]")]
    for status in ("PARTIAL", "UNAVAILABLE", "ERROR"):
        arm = table[table.index(f"R.{status}:") : table.index("\n", table.index(f"R.{status}:"))]
        assert '"passed"' not in arm, f"R.{status} maps to a pass: {arm.strip()}"
    assert 'R.REVIEWED: ("failed" if best.findings else "passed")' in table, table

    # And the exit codes: only a real review with no findings is success.
    exits = src[src.index("exit_code = {") : src.index("]\n", src.index("exit_code = {"))]
    assert "R.UNAVAILABLE: O.NOTHING" in exits, exits
    assert "R.ERROR: O.FAIL" in exits, exits
    assert "R.PARTIAL: O.REFUSED" in exits, exits
    # A status nobody mapped would KeyError at runtime, on the failing path.
    assert {"REVIEWED", "PARTIAL", "UNAVAILABLE", "ERROR"} <= set(dir(R))


def test_cleanup_without_apply_destroys_nothing(repo):
    """`cleanup` is a REPORT unless asked. Nothing probed that.

    Removing the `if apply:` guard made every bare `ddflow cleanup` — and every
    `ddflow_cleanup` call with no arguments — delete worktrees and branches, and the suite
    stayed green. This is the most destructive default in the package and it was one
    `if` away from being on.
    """
    from ddflow import api

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    run_cli(repo, "claim", "T1")
    _code, shown, _ = run_cli(repo, "--json", "show", "T1")
    tree = Path(json.loads(shown)["worktree"])
    assert tree.exists(), "the fixture produced no worktree, so this proves nothing"

    out = api.cleanup(repo)
    assert out.data["applied"] is False, out.data
    assert out.data["performed"] == [], f"a report performed actions: {out.data['performed']}"
    assert tree.exists(), "cleanup deleted a worktree without --apply"


def test_a_max_tasks_flag_overrides_the_config_knob(repo):
    """ "0 means no flag given", so an operator who set `[importer] max_tasks` is not
    silently overruled by an argparse default that looks like a choice and is not one.

    `max_tasks` is a GUARD RAIL, not a "take the first N": over the cap the plan withholds
    the tasks AND their phases and says why, because an import writes events into a log
    that is committed to git and five thousand of them is not recoverable by anything
    short of editing history. The first version of this test assumed a truncating cap and
    failed — the documented behaviour is a refusal.
    """
    from ddflow import api

    run_cli(repo, "init")
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "todo.md").write_text(
        "## Phase one\n\n" + "".join(f"- [ ] task number {i}\n" for i in range(1, 4))
    )
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text() + "\n[importer]\nmax_tasks = 2\n")

    # The KNOB applies when no flag is given: 3 tasks over a cap of 2 is a refusal.
    by_knob = api.import_project(repo)
    assert not [f for f in by_knob.data["found"] if f["kind"] == "task"], (
        "the config knob was ignored — tasks were proposed over the cap"
    )
    assert any("REFUSING" in n for n in by_knob.data["notes"]), by_knob.data["notes"]

    # The FLAG wins when one is given.
    by_flag = api.import_project(repo, max_tasks=5)
    tasks = [f for f in by_flag.data["found"] if f["kind"] == "task"]
    assert len(tasks) == 3, f"the flag did not override the knob: {len(tasks)} tasks"
    assert not any("REFUSING" in n for n in by_flag.data["notes"]), by_flag.data["notes"]


def test_the_out_of_order_warning_reaches_the_AGENT_too(repo):
    """B160. It printed to stderr only, and `_run_cli` captured stdout.

    So an agent recording `rubber_duck` before `implement` was told NOTHING, and the only
    trace was a `gate.out_of_order` event nobody reads back. Third occurrence of the class
    whose comment still sits in `cmd_complete`: "it used to print only in human mode, so an
    agent driving over MCP was never told that a gate had not run."

    Adding a key to a body consumers parse is why this is its own change rather than part
    of the B37 migration. `null` when the recording was in order, so the shape is stable
    and a consumer can branch on it rather than on presence.
    """
    import json as _json

    from ddflow.surfaces.mcp import Server

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    pipeline = _pipeline(repo)
    first, later = pipeline[0], pipeline[-2]

    def record(gate):
        reply = Server(repo).handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "ddflow_gate_record",
                    "arguments": {"id": "T1", "gate": gate, "outcome": "passed", "evidence": "ok"},
                },
            }
        )["result"]
        text = reply["content"][0]["text"]
        return _json.loads(text[text.index("{") :])

    out_of_order = record(later)
    assert "warning" in out_of_order, f"the agent still cannot see it: {out_of_order}"
    assert out_of_order["warning"], out_of_order
    assert first in out_of_order["warning"], out_of_order["warning"]
    assert "enforce_order" in out_of_order["warning"], out_of_order["warning"]

    # In order: the key is still THERE, and null. A consumer branching on presence rather
    # than on truth is the reason projections are shape-stable.
    in_order = record(first)
    assert "warning" in in_order, in_order
    assert not in_order["warning"], in_order


def test_setup_returns_its_checklist_as_PROSE(repo):
    """`ddflow_setup` is text-bodied AND a write, so it has no wire-shape row: running it
    twice adopts the project twice.

    Its body is the checklist of what it wrote and the three steps that follow, which is
    what the string path returned. Asserted directly, because the two lists it cannot be in
    would each have skipped it.
    """
    from ddflow.surfaces.mcp import TOOLS, Server

    assert TOOLS["ddflow_setup"].get("text") is True
    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_setup", "arguments": {"agents": "claude"}},
        }
    )["result"]
    body = reply["content"][0]["text"]
    assert not body.lstrip().startswith(("{", "[")), f"prose expected, got JSON: {body[:120]}"
    assert "ddflow adopted for" in body, body[:300]
    assert "set your test command" in body, body[:300]
