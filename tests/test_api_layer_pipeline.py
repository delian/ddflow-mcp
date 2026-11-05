"""The application layer: the gate pipeline's policy and structural refusals.

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
    OK,
    REFUSED,
    _pipeline,
)
from conftest import run_cli

from ddflow import api

# -- the gate pipeline's policy, which nothing probed -------------------------------------


def test_enforce_order_block_actually_blocks(repo):
    """The order in the pipeline is not decoration.

    A rubber-duck review recorded before `implement` reviewed an empty diff; a `merge`
    recorded before `unit_tests` merged something nobody tested. `block` is the policy
    that says so — and disabling it left the entire suite green, so for as long as it has
    existed nothing has demonstrated that it refuses anything.
    """

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
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

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
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

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
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
    src = (Path(G._query.__file__)).read_text()
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

    run_cli(repo, "init")
    out = api.task_add(repo, "T1", title="orphan", parent="P-NOT-REAL")
    assert out.exit == FAIL, out
    assert "no such parent" in out.reason, out.reason

    _code, rows, _ = run_cli(repo, "--json", "progress")
    assert "T1" not in rows, "the task was added under a parent that does not exist"
