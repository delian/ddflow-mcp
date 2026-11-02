"""Scenario 6 — one project, from an operator's first sentence to a rebuild from the log.

The other scenarios each prove one property. This one is the whole arc, driven the way
a real project moves: an operator says what they want in English, the plan is built
from that, the work happens, requirements CHANGE while tasks are in flight, bugs are
found and fixed, decisions are made and later reversed, and at the end the entire
project is reconstructed from nothing but the event log.

The invented project is `ledger`, a double-entry bookkeeping library:

    P1  core                                        globs ledger/core/**
        P1.T1  money        money.py                 independent
        P1.T2  account      account.py               independent   <- parallel with T1
        P1.T3  entry        entry.py     needs T1,T2  UMBRELLA
            P1.T3.a  posting rules   entry.py
            P1.T3.b  validation      validate.py  needs P1.T3.a
    P2  report                       needs P1        globs ledger/report/**
        P2.T4  summary      summary.py               SPLIT mid-flight into T4a/T4b
        P2.T5  csv export   csv.py       needs T4     ADDED mid-flight, from a new prompt

Every structural property the operator asked for is load-bearing here rather than
decorative:

* **T1 and T2 must go out in parallel**, in separate worktrees, to different agents.
* **P1.T3.a must NOT be offered** while T1 and T2 are open — its umbrella's dependency
  is its dependency, or the phase graph is decoration. (This was a real bug; it is
  `tests/test_inherited_deps.py` now.)
* **P2.T4 must not be offered** while P1 is open, without P2.T4 re-declaring the
  dependency by hand. Restating the thing under test as its own input proves nothing.
* **P2.T5 depends on P2.T4**, and P2.T4 is then split — so T5 must end up waiting on
  both halves, through an umbrella that did not exist when T5 was written.

Nothing is mocked. Each agent is a separate MCP client process talking JSON-RPC to a
separately-spawned server over a pipe, against a real git repository, creating real
worktrees, running real pytest, and making real merges.
"""

from __future__ import annotations

import json

from harness import McpClient, Scenario
from lifecycle_acts_build import act2_phase_one_in_parallel, act3_subtasks_and_a_bug
from lifecycle_acts_change import act4_close_the_phase, act5_requirements_change_mid_flight
from lifecycle_acts_finish import (
    act6_the_guardrails,
    act7_the_operator_asks,
    act8_rebuild_from_the_log,
)
from lifecycle_data import SCAFFOLD, U1, U2
from lifecycle_support import BETA, ROOT


def run(sc: Scenario) -> None:
    sc.head("Scenario 6 — a whole project: plan, build, change course, rebuild")

    repo = sc.make_repo("ledger", SCAFFOLD)
    wt_root = repo / ".ddflow" / "worktrees"  # the default root (D-worktree-home)

    alpha = McpClient(repo, ROOT, agent="alpha")
    beta = McpClient(repo, ROOT, agent="beta")
    BETA[0] = beta
    try:
        act1_the_operator_speaks(sc, alpha, repo)
        act2_phase_one_in_parallel(sc, alpha, beta, repo, wt_root)
        act3_subtasks_and_a_bug(sc, alpha, beta, repo, wt_root)
        act4_close_the_phase(sc, alpha, repo)
        act5_requirements_change_mid_flight(sc, alpha, beta, repo, wt_root)
        act6_the_guardrails(sc, alpha, repo)
        act7_the_operator_asks(sc, alpha, repo)
        act8_rebuild_from_the_log(sc, alpha, repo)
    finally:
        alpha.close()
        beta.close()


# =====================================================================================
# Act 1 — the operator speaks, and a plan exists
# =====================================================================================


def act1_the_operator_speaks(sc, alpha, repo):
    sc.step("The operator opens a session and states the requirement")
    alpha.initialize()
    BETA[0].initialize()
    alpha.tool("ddflow_setup", agents="claude")
    session = alpha.jtool("ddflow_session_start", model="claude-opus-5", tool="claude-code")
    sid = session["session"]
    alpha.tool("ddflow_session_prompt", session=sid, text=U1)
    alpha.tool("ddflow_session_prompt", session=sid, text=U2)
    sc.note(
        "The prompts are recorded verbatim, not summarised. Act 8 rebuilds the project "
        "from them, so a paraphrase written afterwards would be testing the paraphrase."
    )

    sc.step("The agent turns the requirement into a queue")
    alpha.tool("ddflow_phase_add", id="P1", title="core", globs="ledger/core/**")
    alpha.tool("ddflow_phase_add", id="P2", title="report", needs="P1", globs="ledger/report/**")
    for tid, title, needs, globs in (
        ("P1.T1", "money type", "", "ledger/core/money.py,tests/test_money.py"),
        ("P1.T2", "account model", "", "ledger/core/account.py,tests/test_account.py"),
        ("P1.T3", "journal entries", "P1.T1,P1.T2", "ledger/core/entry.py,tests/test_entry.py"),
    ):
        alpha.tool("ddflow_task_add", id=tid, phase="P1", title=title, needs=needs, globs=globs)
    alpha.tool(
        "ddflow_task_add",
        id="P2.T4",
        phase="P2",
        title="balance summary",
        globs="ledger/report/summary.py,tests/test_summary.py",
    )

    sc.step("U2 is an architectural decision, so it is recorded as one")
    alpha.tool(
        "ddflow_decision_add",
        id="D1",
        title="Money is integer minor units",
        context="A ledger must reconcile exactly; binary floats cannot represent 0.10.",
        decision="Money stores an int of minor units and a currency. No float ever enters.",
        consequences="Every amount crossing an API boundary is parsed, never cast.",
        alternatives="rejected: Decimal (still needs a rounding policy per call site); "
        "rejected: float with epsilon comparison (hides the error instead of removing it)",
        globs="ledger/core/money.py,ledger/report/**",
        by="operator",
    )
    applicable = alpha.jtool("ddflow_decision_applicable", id="P1.T1")
    sc.check(
        "the decision is surfaced for the task whose files it governs",
        any(d["id"] == "D1" for d in applicable["applicable"]),
        json.dumps(applicable),
    )
    applicable = alpha.jtool("ddflow_decision_applicable", id="P1.T2")
    sc.check(
        "and NOT for a task it does not govern — scoping by globs, not by broadcast",
        not any(d["id"] == "D1" for d in applicable["applicable"]),
        json.dumps(applicable),
    )

    sc.step("The plan is checked before any work starts")
    plan = alpha.jtool("ddflow_next")
    ready = [r["id"] for r in plan["ready"]]
    blocked = {b["item"]: b for b in plan["blocked"]}
    sc.check(
        "the two independent tasks are offered", sorted(ready) == ["P1.T1", "P1.T2"], str(ready)
    )
    sc.check(
        "the dependent task is withheld and names BOTH blockers",
        sorted(blocked["P1.T3"]["waiting_on"]) == ["P1.T1", "P1.T2"],
        json.dumps(blocked.get("P1.T3")),
    )
    sc.check(
        "P2's task is withheld because its PHASE waits, without re-declaring it",
        "P2.T4" not in ready and "P1" in blocked["P2.T4"]["waiting_on"],
        json.dumps(blocked.get("P2.T4")),
    )
    sc.check(
        "and the refusal says where the inherited dependency came from",
        "P2" in blocked["P2.T4"]["detail"],
        blocked["P2.T4"]["detail"],
    )
    loops = alpha.jtool("ddflow_loops")
    sc.check("a healthy plan reports no loops", not loops, json.dumps(loops))
