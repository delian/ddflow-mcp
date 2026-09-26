"""A checkpoint the agent cannot clear.

Every other gate in this pipeline is satisfied by the agent — it runs a command, or it
asserts it did the thinking. That is right for work whose correctness is checkable
afterwards, and wrong for a plan: by the time an agent has built the wrong thing, the
cost is already paid. A human gate is where the operator says "yes, build that" BEFORE
the compute is spent.

**What this is, precisely.** An audit trail and a speed bump, not a security boundary.
An agent with shell access can run `ddflow approve` itself, and nothing in this design
changes that — the tool does not control the machine.

The guarantee, as narrowly as it holds: no MCP tool records a human outcome, and a
clearance carries the OS user and a `human` flag. The first version of this paragraph
claimed the MCP surface could not satisfy the gate "at all", and roborev showed that
false — `ddflow_configure` could flip `gate.<id>.human` off and `ddflow_gate_record`
then cleared it, no shell involved. The config path is closed now, and the claim is
narrow, because the broad one was the overclaim this project keeps catching elsewhere.

*From `pimzino/spec-workflow-mcp`, whose per-phase human approval was the best idea in
either rival server (R13). Their dashboard is declined and filed; the gate is the part
that changes behaviour.*
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3

GATES_TOML = (
    "[gate.plan_approved]\n"
    'title = "Operator approves the plan"\n'
    "human = true\n"
    'prompt = "Show the operator the plan before building it."\n'
)


def _setup(repo) -> None:
    run_cli(repo, "init")
    (repo / ".ddflow" / "gates.toml").write_text(GATES_TOML)
    run_cli(repo, "workflow", "pipeline", "task", "plan_approved,implement,merge")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")


def _outcome(repo, item="T1", gate="plan_approved"):
    st = fold(EventLog(repo).read_all(), strict=False)
    return st.items[item].gate_outcome(gate)


# -- the agent cannot clear it ---------------------------------------------------------


def test_gate_record_refuses_a_human_gate(repo):
    """The ordinary path an agent would take, closed."""
    _setup(repo)
    code, _out, err = run_cli(
        repo,
        "gate",
        "record",
        "T1",
        "plan_approved",
        "--outcome",
        "passed",
        "--evidence",
        "I read it myself and it is fine",
    )
    assert code == REFUSED, f"expected a refusal, got {code}: {err}"
    assert "human-approval gate" in err
    assert "ddflow approve T1 plan_approved" in err, "refused without naming the way forward"
    assert _outcome(repo) != "passed", "the refusal did not prevent the outcome"


def test_the_refusal_is_a_COORDINATION_code_not_a_failure(repo):
    """Nothing went wrong — the caller is simply not the party who can clear it. An
    agent branching on exit codes has to tell "this is broken" from "this is not mine
    to do", and collapsing them sends it to debug a refusal."""
    _setup(repo)
    code, _out, _err = run_cli(
        repo, "gate", "record", "T1", "plan_approved", "--outcome", "passed", "--evidence", "x"
    )
    assert code == REFUSED and code != FAIL


def test_gate_run_explains_instead_of_calling_it_an_agent_gate(repo):
    """It is neither a command gate nor an agent gate, and describing it as the latter
    would tell the agent to go and record it — which is exactly what it must not do."""
    _setup(repo)
    code, _out, err = run_cli(repo, "gate", "run", "T1", "plan_approved")
    assert code == NOTHING
    assert "HUMAN-APPROVAL" in err
    assert "no MCP tool" in err
    assert "Show the operator the plan" in err, "the gate's own prompt was not shown"


def test_no_mcp_tool_can_clear_a_human_gate(repo):
    """The property the parity exemption claims, asserted end to end rather than by
    the absence of a name in a list.

    Drives EVERY tool that could plausibly write a gate outcome through real JSON-RPC
    and asserts the gate is still unsatisfied afterwards. A future tool that happens to
    reach `gates.record` without the `human` flag fails here.
    """
    from ddflow.surfaces.mcp import TOOLS, Server

    _setup(repo)
    srv = Server(repo)
    candidates = [n for n in TOOLS if "gate" in n or "approve" in n]
    assert candidates, "no gate tools at all; this test proves nothing"

    attempted = []
    for name in candidates:
        for args in (
            {"id": "T1", "gate": "plan_approved", "outcome": "passed", "evidence": "ok"},
            {"id": "T1", "gate": "plan_approved", "reason": "fine by me"},
            {"id": "T1", "gate": "plan_approved"},
        ):
            reply = srv.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": args},
                }
            )
            text = reply["result"]["content"][0]["text"][:80]
            attempted.append((name, text))
            # Checked after EVERY call, not once at the end. The end-state version of
            # this test was green under a mutation that disabled the refusal entirely:
            # `ddflow_gate_record` cleared the gate, and `ddflow_gate_skip` ran
            # afterwards and overwrote the outcome with `skipped`, so the final read
            # never saw it. A later write masking an earlier one is how a whole loop
            # of attempts collapses into one observation.
            assert _outcome(repo) not in ("passed", "skipped"), (
                f"{name} with {args} put the human gate into {_outcome(repo)!r} "
                f"through the MCP surface — reply was: {text}"
            )
    # The attempt has to have REACHED the gate machinery, or this test would pass on a
    # typo'd argument name. Asserted because it nearly did: an earlier version was
    # green under a mutation that disabled the refusal entirely, which means it was
    # measuring nothing.
    assert any("plan_approved" in text for _n, text in attempted), (
        f"no call reached the gate at all, so the refusal was never exercised:\n{attempted}"
    )
    assert _outcome(repo) != "passed", (
        f"a human-approval gate was cleared through the MCP surface:\n{attempted}"
    )


def test_there_is_no_approve_tool_at_all(repo):
    """Belt and braces, and cheap: the exemption says no tool exists, so say it here
    too. If one is ever added deliberately, this is the line that forces the
    conversation."""
    from ddflow.surfaces.mcp import TOOLS

    assert not [n for n in TOOLS if "approve" in n or "approval" in n], sorted(TOOLS)


# -- the person can ---------------------------------------------------------------------


def test_a_person_can_approve_it_and_the_pipeline_advances(repo):
    _setup(repo)
    code, out, err = run_cli(repo, "approve", "T1", "plan_approved", "--note", "read it")
    assert code == OK, err
    assert "approved" in out
    assert _outcome(repo) == "passed"


def test_the_approval_records_WHO_not_the_agent_id(repo):
    """Forgery is not preventable here — an agent with a shell can run this. What is
    preventable is a forged approval being indistinguishable from a real one, so the
    record carries the OS user and a `human` flag rather than the agent identity that
    every other event uses."""
    _setup(repo)
    run_cli(repo, "approve", "T1", "plan_approved", "--note", "looks right")
    rec = fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["plan_approved"]
    assert rec.evidence.get("human") is True, rec.evidence
    assert rec.evidence.get("approved_by") == getpass.getuser(), rec.evidence
    assert rec.evidence.get("note") == "looks right"


def test_a_rejection_is_a_first_class_outcome(repo):
    """ "The operator looked and said no" and "nobody has looked yet" are different
    states. An item sitting in the second forever is how a checkpoint becomes a silent
    stall, so a refusal is recorded rather than expressed as an absence."""
    _setup(repo)
    code, _out, err = run_cli(
        repo, "approve", "T1", "plan_approved", "--reject", "--reason", "wrong approach"
    )
    assert code == OK, err
    assert _outcome(repo) == "failed"
    rec = fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["plan_approved"]
    assert "wrong approach" in rec.reason


def test_a_rejection_without_a_reason_is_refused(repo):
    """A "no" nobody can act on is a stall wearing a decision's clothes."""
    _setup(repo)
    code, _out, err = run_cli(repo, "approve", "T1", "plan_approved", "--reject")
    assert code == FAIL
    assert "reason" in err.lower()
    assert _outcome(repo) != "failed", "the rejection landed despite being refused"


def test_approving_a_gate_that_is_not_a_human_gate_is_refused(repo):
    """Otherwise `approve` becomes a second way to record any gate, with weaker
    evidence requirements than the first — which is how the evidence contract gets
    routed around."""
    _setup(repo)
    code, _out, err = run_cli(repo, "approve", "T1", "implement")
    assert code == FAIL
    assert "not a human-approval gate" in err


def test_an_unknown_gate_is_refused(repo):
    _setup(repo)
    code, _out, err = run_cli(repo, "approve", "T1", "no_such_gate")
    assert code == FAIL
    assert "no such gate" in err


# -- and it blocks completion ------------------------------------------------------------


def test_an_unapproved_item_cannot_complete_when_the_gate_is_required(repo):
    """The point of the whole thing. Without this it is a note in a log."""
    _setup(repo)
    # Reviewer independence is a separate requirement with its own tests; leaving it on
    # would make this test fail for a reason that has nothing to do with the human
    # gate, and a test that can go red for two unrelated causes reports neither.
    with (repo / ".ddflow" / "config.toml").open("a") as fh:
        fh.write("\n[agent]\nreviewer_family_must_differ = false\n")
    run_cli(repo, "workflow", "gate", "plan_approved", "--required")
    run_cli(repo, "claim", "T1", "--no-worktree")
    run_cli(repo, "gate", "record", "T1", "implement", "--outcome", "passed")
    run_cli(repo, "gate", "record", "T1", "merge", "--outcome", "passed")
    code, _out, err = run_cli(repo, "complete", "T1")
    assert code != OK, "completed without the operator's approval"
    assert "plan_approved" in err

    run_cli(repo, "approve", "T1", "plan_approved", "--note", "yes")
    code, _out, err = run_cli(repo, "complete", "T1")
    assert code == OK, err


def test_the_default_pipeline_has_no_human_gate(repo):
    """Opt-in, like every other behaviour change here. A human checkpoint that appears
    unasked turns every existing project's first `complete` into a refusal nobody
    configured."""
    from ddflow.services.gates import DEFAULT_GATES

    human = [g.id for g in DEFAULT_GATES.values() if g.is_human_gate]
    assert not human, f"the shipped pipeline gained a human gate: {human}"


def test_gate_verify_names_it_for_what_it_is(repo):
    """It called a human gate "an agent gate", which carries the wrong advice: an agent
    gate's honesty rests on the evidence contract, and a human gate's rests on a person
    having looked. No mutation can demonstrate the latter."""
    _setup(repo)
    _code, out, err = run_cli(repo, "gate", "verify", "T1", "plan_approved")
    assert "HUMAN-APPROVAL" in (out + err), out + err


def test_an_agent_cannot_SKIP_its_way_past_a_human_gate(repo):
    """The other route around it. `gate skip` is the sanctioned escape hatch for a step
    that genuinely does not apply — but "the operator does not need to approve this" is
    not the agent's call to make, and a skip with a plausible reason would clear the
    checkpoint while looking like process."""
    _setup(repo)
    code, _out, err = run_cli(
        repo, "gate", "skip", "T1", "plan_approved", "--reason", "seems fine to me"
    )
    assert code == REFUSED, f"an agent skipped a human gate: {code} {err}"
    assert _outcome(repo) != "skipped"


# -- what roborev found on the first version ---------------------------------------------


def test_approving_an_unknown_item_is_refused_and_creates_nothing(repo):
    """`approve` was the only gate-writing path with no existence check.

    `_h_gate` folds through `_item`, which CREATES an item for an unknown subject. So a
    typo'd id printed "approved", exited 0, and materialised a phantom task carrying a
    human approval — while the item the operator meant to approve stayed unapproved.
    The one command whose entire value is that a person looked at a SPECIFIC thing.

    *roborev on 137f362, CONFIRMED, reproduced before fixing.*
    """
    _setup(repo)
    code, _out, err = run_cli(repo, "approve", "TYPO-NOT-REAL", "plan_approved", "--note", "x")
    assert code == FAIL, f"approved a nonexistent item: {code}"
    assert "TYPO-NOT-REAL" in err

    st = fold(EventLog(repo).read_all(), strict=False)
    assert "TYPO-NOT-REAL" not in st.items, f"a phantom item was created: {sorted(st.items)}"
    assert _outcome(repo) != "passed", "the real item was left approved by the typo"


def test_a_removed_item_cannot_be_approved(repo):
    """`_require_item` exists because one hand-written copy of the check forgot
    `removed`: the item still folds, so `.get()` finds it, and only the flag says it is
    gone."""
    _setup(repo)
    run_cli(repo, "remove", "T1", "--reason", "not needed")
    code, _out, _err = run_cli(repo, "approve", "T1", "plan_approved", "--note", "x")
    assert code == FAIL


def test_the_gate_instruction_tells_the_agent_to_ASK_not_to_record(repo):
    """The refusal shipped without updating the surface that tells an agent what to do,
    so `gate status` printed `ddflow gate record …` for a gate that refuses it —
    instructing the agent to collect an exit 3 and conclude something is broken."""
    _setup(repo)
    _code, out, err = run_cli(repo, "gate", "status", "T1")
    text = out + err
    assert "ddflow approve T1 plan_approved" in text, text
    assert "gate record T1 plan_approved --outcome passed" not in text, text


def test_the_workflow_description_calls_it_a_human_gate(repo):
    """`ddflow_workflow` is the tool an agent asks "what is the pipeline here?" — the
    worst place to report a human gate as an agent gate, which is what it did."""
    _setup(repo)
    _code, out, _err = run_cli(repo, "workflow")
    line = next(ln for ln in out.splitlines() if "plan_approved" in ln)
    assert "human" in line, line
    assert "agent" not in line, line


def test_the_completion_blocker_names_approve_not_the_refused_commands(repo):
    """The blocker for a silent gate suggested `gate run|record` and `gate skip` — all
    three of which refuse a human gate."""
    _setup(repo)
    run_cli(repo, "claim", "T1", "--no-worktree")
    _code, _out, err = run_cli(repo, "complete", "T1")
    blocker = next(ln for ln in err.splitlines() if "plan_approved" in ln and "awaiting" in ln)
    assert "ddflow approve T1 plan_approved" in blocker, blocker


def test_the_human_flag_cannot_be_flipped_from_the_config_writer(repo):
    """The claim that the MCP surface could not satisfy a human gate was FALSE: two
    calls — `ddflow_configure` setting `gate.<id>.human = false`, then
    `ddflow_gate_record` — cleared it with no shell involved.

    Whether a checkpoint belongs to the operator is not a configurable preference.
    """
    from ddflow.surfaces.mcp import Server

    run_cli(repo, "init")
    run_cli(repo, "config", "--append-toml", '[gate.plan_approved]\nhuman = true\nprompt = "p"')
    run_cli(repo, "workflow", "pipeline", "task", "plan_approved,implement,merge")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")

    srv = Server(repo)

    def call(name, **args):
        r = srv.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": args},
            }
        )
        return r["result"]["content"][0]["text"]

    flip = call("ddflow_configure", set="gate.plan_approved.human", value="false")
    assert "refusing" in flip.lower(), f"the flag was editable from MCP: {flip}"

    call("ddflow_gate_record", id="T1", gate="plan_approved", outcome="passed", evidence="mine")
    assert _outcome(repo) != "passed", "a human gate was cleared through two MCP calls"


def test_gate_verify_on_a_human_gate_is_a_refusal_not_a_failure(repo):
    """Same vocabulary `gate record` uses: nothing is broken, there is simply nothing a
    mutation could demonstrate about whether a person looked. Collapsing it into FAIL
    is the drift this feature argued against one function away."""
    _setup(repo)
    code, _out, _err = run_cli(repo, "gate", "verify", "T1", "plan_approved")
    assert code == REFUSED, f"expected a coordination refusal, got {code}"


def _human_gate_repo(repo) -> None:
    """A human gate declared in CONFIG, which is the configuration `load_gates`
    recommends and therefore the one the bypasses had to be checked against."""
    run_cli(repo, "init")
    run_cli(repo, "config", "--append-toml", '[gate.plan_approved]\nhuman = true\nprompt = "p"')
    run_cli(repo, "workflow", "pipeline", "task", "plan_approved,implement,merge")


def test_workflow_drop_cannot_remove_a_human_gate(repo):
    """Refusing the FLAG was not enough. `workflow drop plan_approved` took the
    checkpoint out of the pipeline entirely, exit 0 — the operator's approval step
    deleted without `human` ever being touched.

    *roborev on 0674a33, CONFIRMED.*
    """
    _human_gate_repo(repo)
    code, out, err = run_cli(repo, "workflow", "drop", "plan_approved")
    assert code != OK, f"a human gate was dropped from the pipeline: {out}"
    assert "human-approval gate" in (out + err)


def test_setting_a_pipeline_that_omits_a_human_gate_is_refused(repo):
    """The same deletion by omission, which is why the check lives on the RESULT at the
    choke point rather than in the `drop` branch: a guard in one branch is a guard the
    other branch does not have."""
    _human_gate_repo(repo)
    code, out, err = run_cli(repo, "workflow", "pipeline", "task", "implement,merge")
    assert code != OK, f"a human gate was omitted out of existence: {out}"
    assert "human-approval gate" in (out + err)


def test_a_pipeline_edit_that_KEEPS_the_human_gate_still_works(repo):
    """The guard must not freeze the pipeline. Reordering or adding around the
    checkpoint is ordinary work."""
    _human_gate_repo(repo)
    code, _out, err = run_cli(
        repo, "workflow", "pipeline", "task", "plan_approved,implement,unit_tests,merge"
    )
    assert code == OK, err


def test_whitespace_and_quoting_cannot_walk_past_the_human_flag_guard(repo):
    """`_toml_upsert` strips the key's segments, so `" gate.x.human"` and
    `gate.x."human"` reach the same TOML key as the bare form. The guard matched the raw
    string, so both missed it.

    *roborev on 0674a33, CONFIRMED gap / THEORETICAL exploit.*
    """
    _human_gate_repo(repo)
    for variant in (
        " gate.plan_approved.human",
        "gate.plan_approved.human ",
        'gate.plan_approved."human"',
    ):
        code, out, err = run_cli(repo, "config", "--set", variant, "false")
        assert code != OK, f"{variant!r} was accepted"
        assert "refusing" in (out + err).lower(), (variant, out, err)
