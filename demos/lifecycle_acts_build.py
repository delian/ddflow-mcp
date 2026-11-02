"""Scenario 6, acts 2-3: two agents build phase one; the dependent task splits."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from harness import McpClient

from lifecycle_data import (
    ACCOUNT_PY,
    ACCOUNT_TEST,
    ENTRY_PY,
    ENTRY_TEST,
    MONEY_PY,
    MONEY_TEST,
    VALIDATE_PY,
    VALIDATE_TEST,
)
from lifecycle_support import ROOT, run_pytest, work

# =====================================================================================
# Act 2 — two agents, two worktrees, one phase
# =====================================================================================


def act2_phase_one_in_parallel(sc, alpha, beta, repo, wt_root):
    sc.step("Two agents claim the two independent tasks at the same time")
    a_claim = alpha.jtool("ddflow_claim", id="P1.T1")
    b_claim = beta.jtool("ddflow_claim", id="P1.T2")
    sc.check(
        "they got different worktrees on different branches",
        a_claim["worktree"] != b_claim["worktree"] and a_claim["branch"] != b_claim["branch"],
        f"{a_claim} / {b_claim}",
    )

    sc.step("A third agent reaching for held work is refused, and offered an alternative")
    gamma = McpClient(repo, ROOT, agent="gamma")
    try:
        gamma.initialize()
        text, code = gamma.tool("ddflow_claim", id="P1.T1")
        sc.check("refused with the coordination exit code, not an error", code == 3, text)
        sc.check("and told who holds it", "alpha" in text, text)
    finally:
        gamma.close()

    sc.step("Both release, then do the work properly through the per-task pipeline")
    alpha.tool("ddflow_release", id="P1.T1")
    beta.tool("ddflow_release", id="P1.T2")
    work(
        sc,
        alpha,
        repo,
        wt_root,
        "P1.T1",
        {"ledger/core/money.py": MONEY_PY, "tests/test_money.py": MONEY_TEST},
    )
    work(
        sc,
        beta,
        repo,
        wt_root,
        "P1.T2",
        {"ledger/core/account.py": ACCOUNT_PY, "tests/test_account.py": ACCOUNT_TEST},
    )

    sc.check(
        "both landed on the base branch",
        (repo / "ledger/core/money.py").is_file() and (repo / "ledger/core/account.py").is_file(),
        sc.git("status", "--short"),
    )


# =====================================================================================
# Act 3 — the dependent task turns out to be two things
# =====================================================================================


def act3_subtasks_and_a_bug(sc, alpha, beta, repo, wt_root):
    sc.step("With T1 and T2 landed, the dependent task opens")
    plan = alpha.jtool("ddflow_next")
    sc.check("P1.T3 is now ready", "P1.T3" in [r["id"] for r in plan["ready"]], str(plan["ready"]))

    sc.step("Working it, the agent finds it is two concerns and adds sub-tasks")
    alpha.tool("ddflow_claim", id="P1.T3")
    alpha.tool(
        "ddflow_task_add",
        id="P1.T3.a",
        parent="P1.T3",
        title="posting rules",
        globs="ledger/core/entry.py,tests/test_entry.py",
    )
    alpha.tool(
        "ddflow_task_add",
        id="P1.T3.b",
        parent="P1.T3",
        title="entry validation",
        needs="P1.T3.a",
        globs="ledger/core/validate.py,tests/test_validate.py",
    )

    shown = alpha.jtool("ddflow_show", id="P1.T3")
    sc.check(
        "adding a child released the parent's lease — an umbrella is not the work",
        not shown.get("lease"),
        json.dumps(shown.get("lease")),
    )
    text, code = alpha.tool("ddflow_claim", id="P1.T3")
    sc.check("and it is no longer claimable at all", code == 3, text)
    sc.check("the refusal names the children to work instead", "P1.T3.a" in text, text)

    plan = alpha.jtool("ddflow_next")
    ready = [r["id"] for r in plan["ready"]]
    blocked = {b["item"]: b for b in plan["blocked"]}
    sc.check("only the unblocked sub-task is offered", ready == ["P1.T3.a"], str(ready))
    sc.check(
        "the dependent sub-task waits on its sibling — sub-tasks carry real dependencies",
        blocked["P1.T3.b"]["waiting_on"] == ["P1.T3.a"],
        json.dumps(blocked.get("P1.T3.b")),
    )

    work(
        sc,
        alpha,
        repo,
        wt_root,
        "P1.T3.a",
        {"ledger/core/entry.py": ENTRY_PY, "tests/test_entry.py": ENTRY_TEST},
    )

    text, code = alpha.tool("ddflow_complete", id="P1.T3", model="claude-opus-5")
    sc.check("the umbrella cannot close while a child is open", code == 3, text)
    sc.check("and it names the child", "P1.T3.b" in text, text)

    sc.step("The second sub-task opens, and working it uncovers a bug in the first")
    plan = alpha.jtool("ddflow_next")
    sc.check("P1.T3.b is offered now", "P1.T3.b" in [r["id"] for r in plan["ready"]], str(plan))

    bug = alpha.jtool("ddflow_bug_fixed" if False else "ddflow_claim", id="P1.T3.b")
    wt = Path(bug["worktree"])
    # The bug: an entry with NO postings totals to zero, so it "balances".
    text, _ = alpha.tool(
        "ddflow_gate_record",
        id="P1.T3.b",
        gate="research",
        outcome="passed",
        evidence="probe: validate(Entry('e', [])) returned True",
    )
    _, out = (
        subprocess.run(
            ["git", "-C", str(wt), "log", "--oneline", "-1"], capture_output=True, text=True
        ).returncode,
        "",
    )
    for rel, body in {
        "ledger/core/validate.py": VALIDATE_PY,
        "tests/test_validate.py": VALIDATE_TEST,
    }.items():
        p = wt / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body.lstrip(), "utf-8")
    sc.commit_in(wt, "P1.T3.b: validation, and the empty-entry guard")

    code, out = run_pytest(wt)
    sc.check("the regression test passes with the fix in place", code == 0, out[-800:])

    sc.step("A bug may not be closed without the test that proves it stays fixed")
    text, code = alpha.tool("ddflow_bug_fixed", id="B1")
    sc.check("closing a bug with no regression test is refused", code != 0, text)

    for gate, kw in (
        ("rules", {"evidence": "D1 applies"}),
        ("implement", {}),
        ("rubber_duck", {"evidence": "refutation attempt", "model": "qwen3-coder"}),
        ("critic", {"evidence": "agrees", "model": "gemini-2.5-pro"}),
        ("standards", {"outcome": "unavailable", "reason": "roborev absent"}),
        (
            "unit_tests",
            {"evidence": out[-300:], "command": "python -m pytest -q", "exit_code": "0"},
        ),
        ("bug_hunt", {"evidence": "found B1: empty entry balanced vacuously"}),
        ("dedupe", {"evidence": "validate() is the only balance check"}),
        ("merge", {}),
    ):
        alpha.tool(
            "ddflow_gate_record",
            id="P1.T3.b",
            gate=gate,
            outcome=kw.pop("outcome", "passed"),
            **kw,
        )
    alpha.tool("ddflow_merge", id="P1.T3.b")
    text, code = alpha.tool("ddflow_complete", id="P1.T3.b", model="claude-opus-5")
    sc.check("P1.T3.b completes", code == 0, text)

    sc.step("Now the umbrella closes, and the lesson survives the task that produced it")
    # Closing the last child closed the umbrella; none of its gates is left silent
    # (Bd37a921536): each is rolled up from the children, naming them, on the record.
    text, code = alpha.tool("ddflow_show", id="P1.T3")
    sc.check("the umbrella closed with its last child", code == 0 and "done" in text, text)
    text, code = alpha.tool("ddflow_gate_status", id="P1.T3")
    sc.check("no gate of the umbrella is left silent", "never run" not in text, text)
    text, code = alpha.tool(
        "ddflow_gate_record", id="P1.T3", gate="standards", outcome="passed", evidence="late"
    )
    sc.check("a finished umbrella takes no more gates, like any item", code != 0, text)
