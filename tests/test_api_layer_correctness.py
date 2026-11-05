"""The application layer: correctness probes a parity test cannot reach.

Split from tests/test_api_layer.py (rule r-small-files); helpers live in api_layer_support.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from api_layer_support import (
    FAIL,
    MIGRATED_WIRE_SHAPES,
    NOTHING,
    OK,
    TEXT_BODIED,
)
from conftest import run_cli

from ddflow import api
from ddflow.core import outcome as O
from ddflow.surfaces.mcp import TOOLS

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

    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "P")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--phase", "P1", "--globs", "a.py")
    run_cli(repo, "task", "add", "T2", "--title", "T2 fixture", "--phase", "P1", "--globs", "b.py")

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

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
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

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
    run_cli(repo, "claim", "T1")

    wt = api.show(repo, "T1").data["item"]["worktree"]
    assert wt, "the claimed item has no worktree at all"
    assert os.path.isabs(wt), f"{wt!r} is relative; a caller cannot cd to it"


def test_nothing_to_recover_is_exit_2_not_success(repo):
    """ "No data" is reported as itself. A clean repo and a repo whose sweep failed must
    not look the same to a caller that only reads the exit code."""

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
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
    from ddflow.surfaces.mcp import Server

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
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
