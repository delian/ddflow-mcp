"""Scenario 6, acts 6-8: the guardrails, the operator's questions, the rebuild from the log."""

from __future__ import annotations

import json
import shutil
import subprocess

from lifecycle_data import U1, U2, U3, U4, U5

# =====================================================================================
# Act 6 — the guardrails, tested by trying to break them
# =====================================================================================


def act6_the_guardrails(sc, alpha, repo):
    sc.step("A circular dependency is detected, named, and refused")
    alpha.tool("ddflow_phase_add", id="PX", title="a bad plan", globs="x/**")
    for tid, needs in (("X.A", "X.C"), ("X.B", "X.A"), ("X.C", "X.B")):
        alpha.tool(
            "ddflow_task_add",
            id=tid,
            phase="PX",
            title=f"ring member {tid}",
            needs=needs,
            allow_unknown_needs=True,
            globs=f"x/{tid}.py",
        )
    loops = alpha.jtool("ddflow_loops")
    cycles = [f for f in loops if f["kind"] == "dependency_cycle"]
    sc.check("the ring is reported", cycles, json.dumps(loops))
    sc.check(
        "with every member named, so it can actually be broken",
        all(n in cycles[0]["detail"] for n in ("X.A", "X.B", "X.C")),
        cycles[0]["detail"],
    )
    text, code = alpha.tool("ddflow_claim", id="X.A")
    sc.check("and nothing in the ring may be claimed", code == 3, text)

    sc.step("Breaking one edge is enough")
    alpha.tool("ddflow_update", id="X.A", needs="")
    loops = alpha.jtool("ddflow_loops")
    sc.check(
        "the cycle is gone",
        not [f for f in loops if f["kind"] == "dependency_cycle"],
        json.dumps(loops),
    )
    for tid in ("X.A", "X.B", "X.C"):
        alpha.tool("ddflow_remove", id=tid, reason="the bad plan was a demonstration", force=True)
    alpha.tool("ddflow_remove", id="PX", reason="ditto", force=True)

    sc.step("A removed item stops generating findings nobody can act on")
    loops = alpha.jtool("ddflow_loops")
    sc.check(
        "no finding names a removed item",
        not [f for f in loops if f["item"].startswith("X.")],
        json.dumps(loops),
    )


# =====================================================================================
# Act 7 — the operator asks questions and gets answers
# =====================================================================================


def act7_the_operator_asks(sc, alpha, repo):
    sc.step("U5: 'what is the status of the project?'")
    sid = alpha.jtool("ddflow_session_start", model="claude-opus-5")["session"]
    alpha.tool("ddflow_session_prompt", session=sid, text=U5)
    status = alpha.jtool("ddflow_status")
    sc.check(
        "one phase closed, one still open",
        status["phases"]["done"] == 1 and status["phases"]["total"] == 2,
        json.dumps(status["phases"]),
    )
    sc.check(
        "and the task counts are real, not a guess",
        status["tasks"]["done"] >= 7,
        json.dumps(status["tasks"]),
    )

    sc.step("'have we been here before?' — one search across everything recorded")
    recall = alpha.jtool("ddflow_recall", query="float money rounding")
    blob = json.dumps(recall)
    sc.check(
        "the architectural decision is found",
        any(h["id"] == "D1" for h in recall.get("decisions", [])),
        blob[:400],
    )
    sc.check(
        "and the operator's own words come back with it",
        "Rounding errors" in blob,
        blob[:500],
    )
    sc.note(f"sources with hits: {sorted(k for k, v in recall.items() if v)}")

    sc.step("The work done is derived from the log, not from anyone's memory")
    progress = alpha.jtool("ddflow_progress")
    commits = sum(r.get("commits", 0) for r in progress)
    attempts = sum(r.get("attempts", 0) for r in progress)
    sc.check(
        "it counts real commits, derived from the log", commits > 0, json.dumps(progress)[:400]
    )
    sc.check("and real claim attempts", attempts > 0, str(attempts))


# =====================================================================================
# Act 8 — lose the code, keep the log
# =====================================================================================


def act8_rebuild_from_the_log(sc, alpha, repo):
    sc.step("Every source file is deleted. Only .ddflow/ survives.")
    # The task worktrees live under .ddflow/worktrees (D-worktree-home) and hold copies
    # of the code: they are checkouts, not the log, so they go too.
    trees = repo / ".ddflow" / "worktrees"
    if trees.exists():
        shutil.rmtree(trees)
        subprocess.run(["git", "-C", str(repo), "worktree", "prune"], check=True)
    for path in sorted(repo.rglob("*.py")):
        if ".ddflow" not in path.parts and ".git" not in path.parts:
            path.unlink()
    remaining = [p for p in repo.rglob("*.py") if ".git" not in p.parts]
    sc.check("the code is genuinely gone", not remaining, str(remaining))

    sc.step("`replay` reconstructs the instruction history from the log alone")
    text, code = alpha.tool("ddflow_replay")
    sc.check("it runs against a repository with no source in it", code == 0, text[:300])
    for label, prompt in (("U1", U1), ("U2", U2), ("U3", U3), ("U4", U4)):
        sc.check(
            f"the operator's {label} survives verbatim",
            prompt[:60] in text,
            f"missing: {prompt[:60]!r}",
        )
    sc.check(
        "the architectural decision survives, with the alternatives it rejected",
        "Money is integer minor units" in text and "Decimal" in text,
        text[:600],
    )
    sc.check(
        "the task graph survives, sub-tasks and all",
        all(t in text for t in ("P1.T3.b", "P2.T4a", "P2.T5")),
        text[:600],
    )
    sc.check(
        "and the bug hunt's lesson survives the code that caused it",
        "empty" in text.lower() or "vacuous" in text.lower() or "B1" in text,
        text[-800:],
    )
    sc.note(
        "That is the claim in full: the prompts, the decisions and their rejected "
        "alternatives, the plan as it actually evolved, and the lessons — enough for "
        "an agent to rebuild the project without the project."
    )
