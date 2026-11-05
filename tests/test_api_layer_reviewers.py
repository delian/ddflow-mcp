"""The application layer: an unavailable reviewer is never a passed review.

Split from tests/test_api_layer.py (rule r-small-files); helpers live in api_layer_support.py.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api_layer_support import (
    FAIL,
    NOTHING,
    _pipeline,
)
from conftest import run_cli

from ddflow import api
from ddflow.surfaces.mcp import TOOLS

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

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")

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

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
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

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
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

    Only REVIEWED may produce `passed`, and on a first review only when it found nothing.
    """
    import inspect

    from ddflow.api import review as A
    from ddflow.config import Config
    from ddflow.services import review as R

    def outcome(status: int, *findings: R.Finding) -> str:
        res = R.ReviewResult("r", "m", "f", status=status, findings=list(findings))
        return A._gate_outcome(res, [res], None, "", "critic", False, Config(), None, print)[0]

    # `review` dispatches one gate or several; the one-gate run takes its outcome from
    # `_gate_outcome`, which holds the table.
    src = inspect.getsource(A._review_gate)
    assert "_gate_outcome(" in src
    assert outcome(R.PARTIAL) == "partial"
    assert outcome(R.UNAVAILABLE) == "unavailable"
    assert outcome(R.ERROR) == "unavailable"
    assert outcome(R.REVIEWED) == "passed"
    assert outcome(R.REVIEWED, R.Finding("HIGH", "t")) == "failed"

    # And the exit codes: only a real review with no findings is success.
    exits = src[src.index("exit_code = {") : src.index("]\n", src.index("exit_code = {"))]
    assert "RV.UNAVAILABLE: O.NOTHING" in exits, exits
    assert "RV.ERROR: O.FAIL" in exits, exits
    assert "RV.PARTIAL: O.REFUSED" in exits, exits
    # A status nobody mapped would KeyError at runtime, on the failing path.
    assert {"REVIEWED", "PARTIAL", "UNAVAILABLE", "ERROR"} <= set(dir(R))


def test_cleanup_without_apply_destroys_nothing(repo):
    """`cleanup` is a REPORT unless asked. Nothing probed that.

    Removing the `if apply:` guard made every bare `ddflow cleanup` — and every
    `ddflow_cleanup` call with no arguments — delete worktrees and branches, and the suite
    stayed green. This is the most destructive default in the package and it was one
    `if` away from being on.
    """

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
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
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
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
    from ddflow.surfaces.mcp import Server

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
