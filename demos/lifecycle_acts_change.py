"""Scenario 6, acts 4-5: the phase-level pass; requirements change mid-flight."""

from __future__ import annotations

import json
import subprocess
import sys

from lifecycle_data import AGGREGATE_PY, CSV_PY, CSV_TEST, FORMAT_PY, SUMMARY_TEST, U3, U4
from lifecycle_support import work

# =====================================================================================
# Act 4 — the phase-level pass
# =====================================================================================


def act4_close_the_phase(sc, alpha, repo):
    sc.step("The phase has its own pipeline, and it is not the task one")
    # `gate status` returns PROSE deliberately — it carries the next gate's instruction,
    # which is the half an agent acts on. The pipeline is readable straight out of it.
    status, _ = alpha.tool("ddflow_gate_status", id="P1")
    sc.check(
        "the phase pipeline has the live smoke run and the fan-out point",
        "live_test" in status and "tasks" in status,
        status[:300],
    )
    sc.check(
        "and not the per-task steps that make no sense for a phase",
        "implement" not in status.split("**")[0],
        status[:300],
    )
    sc.check(
        "and it hands the agent the instruction for the gate it is on",
        "falsifiable" in status.lower() or "refute" in status.lower(),
        status[:400],
    )

    text, code = alpha.tool("ddflow_complete", id="P1", model="claude-opus-5")
    sc.check("the phase will not close on its tasks alone", code == 3, text)
    sc.check(
        "and it lists every phase-level step that has no outcome",
        all(g in text for g in ("research", "bug_hunt", "dedupe", "live_test")),
        text,
    )

    sc.step("The phase-level passes run for real")
    live = subprocess.run(
        [
            sys.executable,
            "-c",
            "from ledger.core.money import Money;"
            "from ledger.core.account import Account;"
            "from ledger.core.entry import Entry, Posting;"
            "from ledger.core.validate import validate;"
            "a=Account('1000','Cash','asset');"
            "e=Entry('sale',[Posting(a,Money.parse('10.00')),Posting(a,Money.parse('-10.00'))]);"
            "print('balanced:', validate(e))",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=120,
    )
    sc.check("the feature actually runs end to end", live.returncode == 0, live.stderr)
    sc.check("and produced the expected output", "balanced: True" in live.stdout, live.stdout)

    for gate, ev in (
        ("research", "phase-level: the double-entry invariant is one rule, in one place"),
        ("tasks", "all member tasks done"),
        ("unit_tests", "full suite green across the phase"),
        ("bug_hunt", "swept the phase: 1 found (B1), 1 fixed with a regression test"),
        ("dedupe", "no duplicated balance logic across money/entry/validate"),
        ("live_test", live.stdout.strip()),
        ("corrections", "B1's fix applied and merged"),
        ("merge", "all task branches landed"),
    ):
        alpha.tool("ddflow_gate_record", id="P1", gate=gate, outcome="passed", evidence=ev)
    text, code = alpha.tool(
        "ddflow_gate_skip",
        id="P1",
        gate="docs",
        reason="Documentation work is outside the scope of this lifecycle scenario.",
    )
    sc.check("the docs gate is explicitly skipped", code == 0, text[:200])
    # phase_full_suite defaults to "require": record a full-suite pass on the merged tree
    # so the default close check stays exercised end to end (B7f54dbf1e3).
    sc.ddflow("ci", "run", "--full", "--command", "python3 -m pytest tests/ -q")
    text, code = alpha.tool("ddflow_complete", id="P1", model="claude-opus-5")
    sc.check("P1 closes", code == 0, text)


# =====================================================================================
# Act 5 — the requirements change while work is in flight
# =====================================================================================


def act5_requirements_change_mid_flight(sc, alpha, beta, repo, wt_root):
    sc.step("P1 is done, so P2's tasks unblock — the inherited dependency released")
    plan = alpha.jtool("ddflow_next")
    sc.check(
        "P2.T4 is offered now", "P2.T4" in [r["id"] for r in plan["ready"]], str(plan["ready"])
    )

    sc.step("The agent starts P2.T4. Mid-flight, the operator adds a requirement (U3)")
    alpha.tool("ddflow_claim", id="P2.T4")
    sid = alpha.jtool("ddflow_session_start", model="claude-opus-5")["session"]
    alpha.tool("ddflow_session_prompt", session=sid, text=U3)
    alpha.tool(
        "ddflow_task_add",
        id="P2.T5",
        phase="P2",
        title="CSV export",
        needs="P2.T4",
        globs="ledger/report/csv_export.py,tests/test_csv.py",
    )
    plan = alpha.jtool("ddflow_next")
    blocked = {b["item"]: b for b in plan["blocked"]}
    sc.check(
        "a task added while another is IN FLIGHT is accepted and correctly withheld",
        "P2.T5" in blocked and "P2.T4" in blocked["P2.T5"]["waiting_on"],
        json.dumps(blocked.get("P2.T5")),
    )

    sc.step("Then the operator says the in-flight task is really two (U4) — split in place")
    alpha.tool("ddflow_session_prompt", session=sid, text=U4)
    before = alpha.jtool("ddflow_show", id="P2.T4")
    text, code = alpha.tool(
        "ddflow_split",
        id="P2.T4",
        into="P2.T4a=aggregate balances,P2.T4b=format the table",
    )
    sc.check("the split succeeds on a claimed, in-flight task", code == 0, text)

    after = alpha.jtool("ddflow_show", id="P2.T4")
    sc.check(
        "the task keeps its id and its history — the thread from plan to work is intact",
        after["id"] == before["id"] and after["created_at"] == before["created_at"],
        f"{before.get('created_at')} vs {after.get('created_at')}",
    )
    sc.check(
        "its lease was released, or the umbrella would block its own children",
        not after.get("lease"),
        json.dumps(after.get("lease")),
    )
    alpha.tool("ddflow_update", id="P2.T4a", globs="ledger/report/aggregate.py")
    alpha.tool("ddflow_update", id="P2.T4b", globs="ledger/report/format.py", needs="P2.T4a")

    plan = alpha.jtool("ddflow_next")
    ready = [r["id"] for r in plan["ready"]]
    blocked = {b["item"]: b for b in plan["blocked"]}
    sc.check("the first half is offered", "P2.T4a" in ready, str(ready))
    sc.check("the second waits on it", blocked["P2.T4b"]["waiting_on"] == ["P2.T4a"], str(blocked))
    sc.check(
        "and P2.T5 still waits on P2.T4 — now an umbrella that did not exist when it was written",
        "P2.T4" in blocked["P2.T5"]["waiting_on"],
        json.dumps(blocked.get("P2.T5")),
    )

    sc.step("Both halves are built; the second reads the first rather than recomputing")
    work(sc, alpha, repo, wt_root, "P2.T4a", {"ledger/report/aggregate.py": AGGREGATE_PY})
    work(
        sc,
        beta,
        repo,
        wt_root,
        "P2.T4b",
        {"ledger/report/format.py": FORMAT_PY, "tests/test_summary.py": SUMMARY_TEST},
    )

    text, code = alpha.tool("ddflow_show", id="P2.T4")
    sc.check("the split umbrella closes when both halves do", "done" in text, text)
    text, code = alpha.tool("ddflow_gate_status", id="P2.T4")
    sc.check(
        "and its gates are rolled up from the halves, none silent", "never run" not in text, text
    )

    plan = alpha.jtool("ddflow_next")
    sc.check(
        "and the mid-flight task finally opens, its dependency satisfied through the split",
        "P2.T5" in [r["id"] for r in plan["ready"]],
        str(plan["ready"]),
    )
    work(
        sc,
        alpha,
        repo,
        wt_root,
        "P2.T5",
        {"ledger/report/csv_export.py": CSV_PY, "tests/test_csv.py": CSV_TEST},
    )
