"""Shared helpers and wire-shape tables for the tests/test_api_layer_*.py modules."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow import api
from ddflow.surfaces.declared import all_commands
from ddflow.surfaces.mcp import TOOLS

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3

#: Which measured keys each tool may have, BY TOOL: two runs of the same command cannot
#: agree on a wall-clock reading, so the comparison must not either. `bisect` records
#: each run's wall time and `rebuild` its total, and under suite load the two surfaces
#: round them differently (0.0 vs 0.01), failing a parity test on a float no caller can
#: rely on (bug Ba0904a2430). Scoped by tool on purpose: a tool that later adds a
#: CONTRACT field named `seconds` must not stop being compared (roborev on 3ea8143b).
_MEASURED_KEYS = {
    "ddflow_bisect": frozenset({"seconds"}),
    "ddflow_rebuild": frozenset({"seconds"}),
    # Ages are measured against the clock of each call.
    "ddflow_lanes": frozenset({"now", "for_s", "heartbeat_age_s", "since"}),
    "ddflow_metrics": frozenset({"now"}),
}
_MEASURED = "\x00measured"


def _without_measured(value, measured=frozenset()):
    """`value` with every measured key's value replaced, every key KEPT.

    The replacement keeps the VALUE'S TYPE (`(_MEASURED, "float")`), so a real `None`
    against a float still fails, and keeping the key means a missing run or an added
    field still compares unequal.
    """
    if isinstance(value, dict):
        return {
            key: (
                (_MEASURED, type(item).__name__)
                if key in measured
                else _without_measured(item, measured)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_without_measured(item, measured) for item in value]
    return value


def _surfaces_agree(tool, a, b):
    """The parity comparison, in ONE place so the regression test exercises the same
    call the migrated-tool test runs, not a copy of it (roborev on 3ea8143b)."""
    measured = _MEASURED_KEYS.get(tool, frozenset())
    return _without_measured(a, measured) == _without_measured(b, measured)


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
    "ddflow_health": (["health"], {}),
    "ddflow_watch": (["watch", "--pid", "999999"], {"pid": 999999}),
    "ddflow_docs": (["docs", "check"], {"verb": "check"}),
    "ddflow_record": (["record", "risk", "list"], {"args": ["risk", "list"]}),
    "ddflow_progress": (["progress"], {}),
    "ddflow_decision_list": (["decision", "list"], {}),
    "ddflow_decision_show": (["decision", "show", "D1"], {"id": "D1"}),
    "ddflow_decision_applicable": (["decision", "applicable", "T1"], {"id": "T1"}),
    "ddflow_workflow": (["workflow"], {}),
    "ddflow_workflow_state": (["workflow", "state"], {}),
    "ddflow_verify": (["verify", "T1"], {"id": "T1"}),
    "ddflow_ci": (["ci", "status"], {"action": "status"}),
    "ddflow_bug_file_tasks": (["bug", "file-tasks"], {}),
    "ddflow_status": (["status"], {}),
    "ddflow_onboard": (["onboard"], {}),
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
    "ddflow_gate_list": (["gate", "list"], {}),
    "ddflow_queue": (["queue", "status"], {"action": "status"}),
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
    "ddflow_dupes": (["dupes"], {}),
    "ddflow_reviewers_list": (["reviewers", "list"], {}),
    "ddflow_cleanup": (["cleanup"], {}),
    "ddflow_cadence": (["cadence"], {}),
    "ddflow_lanes": (["lanes"], {}),
    "ddflow_probe": (["probe", "x"], {"id": "x"}),
    "ddflow_metrics": (["metrics"], {}),
    # The fixture repository has no suite, so this is the exit-2 body; the OK body is compared
    # against the CLI's in tests/test_prose_pins.py.
    "ddflow_pins": (["pins", "README.md"], {"document": "README.md"}),
    # The fixture's only change is uncommitted setup, so this compares the no-test body.
    "ddflow_tests": (["tests"], {}),
    # Without `write` it only proposes, so both surfaces see the same repository.
    "ddflow_precommit": (["precommit"], {}),
    "ddflow_import_verify": (["import", "--verify"], {}),
    "ddflow_companions": (["companions", "list"], {}),
    # A command that cannot be spawned makes the result deterministic on both surfaces
    # (state `unavailable`, one run that could not be made); a real run would not be.
    "ddflow_bisect": (
        ["bisect", "tests/t.py::t", "--cmd", "no-such-runner-xyz {tests}"],
        {"victim": "tests/t.py::t", "cmd": "no-such-runner-xyz {tests}"},
    ),
    # Verifying SPAWNS the servers, and the fixture's cache says the shipped ones are
    # installed, so a default run would start real `npx`/`docker` children whose timings
    # differ between the two calls. Naming a cli companion launches nothing: both surfaces
    # report the same `skipped` list and exit 2.
    "ddflow_companions_verify": (["companions", "--verify", "--id", "optmem"], {"id": "optmem"}),
    # The plan only reads, so both surfaces see the same project.
    "ddflow_upgrade": (["upgrade"], {}),
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
    "ddflow_rule_sync": (["rule", "sync"], {}),
    "ddflow_rule_show": (["rule", "show", "r-test"], {"id": "r-test"}),
}
# A new command declares its own row (`Command.wire_shape`) instead of editing this table.
MIGRATED_WIRE_SHAPES.update(
    {c.tool: c.wire_shape for c in all_commands() if c.tool and c.wire_shape}  # type: ignore[misc]
)

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
    "ddflow_gate_list",
    "ddflow_board",
    "ddflow_replay",
    "ddflow_render",
}

WRITES_NOT_COMPARABLE = {
    # Many edits / a fold in one transaction: a second call finds nothing to do. Pinned by
    # tests/test_batch_fold.py.
    "ddflow_batch",
    "ddflow_task_fold",
    "ddflow_rename",  # a second call finds the old id abandoned; tests/test_rename_item.py
    "ddflow_promote_add",
    # Reads or WRITES the forge (a reply, a resolve); pinned by tests/test_review_threads.py.
    "ddflow_pr_threads",
    "ddflow_promote_deployed",  # appends a deploy record each call; pinned by tests/test_environments.py
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
    # Writes an `item.completed`: a second call finds the phase already closed and is
    # refused, so invoking it twice is not the same as invoking it once.
    "ddflow_phase_close",
    # Writes a `phase.completed` retire: a second call finds the phase already done.
    "ddflow_phase_complete",
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
    "ddflow_link",
    "ddflow_research_add",
    "ddflow_bug_found",
    "ddflow_bug_fixed",
    "ddflow_bug_invalid",
    "ddflow_session_start",
    "ddflow_session_prompt",
    "ddflow_session_note",
    "ddflow_session_end",
    # ask/message/withdraw write (file a request, send a lane message, take an ask back), so
    # a second call differs by construction. message still has its CLI twin (`session
    # message`); ask/asks/withdraw declare `path=()` (folded into `ddflow_session` by D-RA-6;
    # the CLI face is `approvals list`, a different shape), so no CLI argv exists there for a
    # `MIGRATED_WIRE_SHAPES` row — the no-CLI-argv ground alone exempts the read `asks`. Shapes
    # pinned by tests/test_approvals_mcp.py and tests/test_wake_reasons.py.
    "ddflow_session_ask",
    "ddflow_session_asks",
    "ddflow_session_message",
    "ddflow_session_withdraw",
    "ddflow_review",
    "ddflow_review_triage",
    "ddflow_reviewers_detect",
    "ddflow_import",
    "ddflow_companions_add",
    "ddflow_rule_add",
    "ddflow_rule_edit",
    "ddflow_rule_remove",
    # Restarts / ends THIS server (re-exec in place, or end the process): invoking it twice
    # is not the same as invoking it once — the second call finds the server already
    # re-exec'd/ended — and its CLI twin runs in a separate process whose hosting detects
    # differently, so the two surfaces cannot be invoked identically. Pinned by
    # tests/test_mcp_lifecycle.py instead. (B-mcp-server-lifecycle)
    "ddflow_restart",
    "ddflow_kill",
}


def _pipeline(repo):
    return api.workflow_show(repo).data["task_pipeline"]
