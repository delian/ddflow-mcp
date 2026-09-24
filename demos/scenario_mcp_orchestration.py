"""Scenario 5 — a whole multi-phase project, driven end to end over MCP.

This is the scenario the others are pieces of. Two agents, each a separate MCP client
process, build a real Python library across two dependent phases: they bootstrap the
project, configure it, discover a local reviewer, fill the queue, fan out on independent
tasks, run real tests, get refused by the enforcement hook, merge, and close both phases
— **without a single CLI call**. Everything goes through JSON-RPC, because that is the
surface an agent actually uses.

The invented project is `taskmetrics`: parse duration strings, compute statistics, and
render a report. It is chosen so the dependency graph is real rather than decorative —

    P1  core
        T1 parse   (taskmetrics/parse.py)      independent
        T2 stats   (taskmetrics/stats.py)      independent
        T3 cli     (taskmetrics/cli.py)        needs T1 AND T2
    P2  report     needs P1
        T4 render  (taskmetrics/report.py)

— so the run must show T1 and T2 going out in parallel, T3 withheld until both land,
and P2 withheld until P1 closes. The code is small but genuinely functional, and every
test that runs is a real test of real behaviour.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from harness import Fail, McpClient, Scenario

ROOT = Path(__file__).resolve().parents[1]

SCAFFOLD = {
    ".gitignore": "__pycache__/\n*.pyc\n.pytest_cache/\n.orchard-worktrees/\n",
    "README.md": "# taskmetrics\n\nDuration parsing and task statistics.\n",
    "taskmetrics/__init__.py": "",
    "tests/__init__.py": "",
}

# --- the code the "agents" write, kept here so the scenario reads as a narrative ------

PARSE_PY = '''
"""Parse human duration strings into seconds."""

import re

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_TOKEN = re.compile(r"(\\d+(?:\\.\\d+)?)\\s*([smhd])", re.I)


def parse_duration(text):
    """'1h30m' -> 5400.0. Raises ValueError on anything it cannot parse."""
    if not isinstance(text, str):
        raise TypeError("duration must be a string")
    stripped = text.strip().lower()
    if not stripped:
        raise ValueError("empty duration")
    total, consumed = 0.0, 0
    for m in _TOKEN.finditer(stripped):
        total += float(m.group(1)) * _UNITS[m.group(2)]
        consumed += len(m.group(0))
    if consumed != len(stripped.replace(" ", "")):
        raise ValueError(f"cannot parse duration: {text!r}")
    return total
'''

PARSE_TEST = """
import pytest

from taskmetrics.parse import parse_duration


@pytest.mark.parametrize("text,want", [
    ("30s", 30.0), ("5m", 300.0), ("1h", 3600.0), ("2d", 172800.0),
    ("1h30m", 5400.0), ("1h 30m 15s", 5415.0), ("1.5h", 5400.0),
])
def test_parses_known_forms(text, want):
    assert parse_duration(text) == want


@pytest.mark.parametrize("bad", ["", "  ", "1x", "abc", "1h!", "h"])
def test_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_duration(bad)


def test_rejects_non_string():
    with pytest.raises(TypeError):
        parse_duration(90)
"""

STATS_PY = '''
"""Summary statistics over a sequence of durations."""

import math


def summarise(values):
    """Return count/total/mean/median/p95 for a sequence of numbers.

    An EMPTY sequence returns zeros rather than raising: a report over a day with no
    tasks is a legitimate report, and making the caller special-case it pushes the same
    branch into every call site.
    """
    nums = [float(v) for v in values]
    if not nums:
        return {"count": 0, "total": 0.0, "mean": 0.0, "median": 0.0, "p95": 0.0}
    ordered = sorted(nums)
    n = len(ordered)
    mid = n // 2
    median = ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    # Nearest-rank p95, clamped: for n < 20 the rank rounds past the end.
    rank = max(0, min(n - 1, math.ceil(0.95 * n) - 1))
    return {"count": n, "total": sum(ordered), "mean": sum(ordered) / n,
            "median": median, "p95": ordered[rank]}
'''

STATS_TEST = """
from taskmetrics.stats import summarise


def test_empty_is_zeros_not_an_error():
    assert summarise([])["count"] == 0
    assert summarise([])["mean"] == 0.0


def test_single_value():
    s = summarise([10])
    assert s["count"] == 1 and s["mean"] == 10.0 and s["p95"] == 10.0


def test_odd_and_even_medians():
    assert summarise([1, 2, 3])["median"] == 2
    assert summarise([1, 2, 3, 4])["median"] == 2.5


def test_p95_never_runs_off_the_end():
    for n in range(1, 25):
        s = summarise(list(range(n)))
        assert s["p95"] <= n - 1


def test_total_and_mean():
    s = summarise([2, 4, 6])
    assert s["total"] == 12 and s["mean"] == 4
"""

CLI_PY = '''
"""Command line: read durations, print a summary."""

import argparse
import json
import sys

from taskmetrics.parse import parse_duration
from taskmetrics.stats import summarise


def main(argv=None):
    ap = argparse.ArgumentParser(prog="taskmetrics")
    ap.add_argument("durations", nargs="*", help="e.g. 1h30m 45m 2h")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        seconds = [parse_duration(d) for d in args.durations]
    except (ValueError, TypeError) as exc:
        print(f"taskmetrics: {exc}", file=sys.stderr)
        return 2
    stats = summarise(seconds)
    if args.json:
        print(json.dumps(stats, indent=2))
    else:
        print(f"count  {stats['count']}")
        print(f"total  {stats['total']:.0f}s")
        print(f"mean   {stats['mean']:.0f}s")
        print(f"median {stats['median']:.0f}s")
        print(f"p95    {stats['p95']:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

CLI_TEST = """
from taskmetrics.cli import main


def test_runs_and_reports(capsys):
    assert main(["1h", "30m"]) == 0
    out = capsys.readouterr().out
    assert "count  2" in out and "total  5400s" in out


def test_json_output(capsys):
    import json
    assert main(["1h", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["count"] == 1


def test_bad_input_exits_two(capsys):
    assert main(["not-a-duration"]) == 2
    assert "cannot parse" in capsys.readouterr().err


def test_no_arguments_is_an_empty_summary(capsys):
    assert main([]) == 0
    assert "count  0" in capsys.readouterr().out
"""


def _commit_in(sc: Scenario, worktree: Path, message: str, agent: str = "") -> None:
    """Commit inside a worktree, exactly as an agent would.

    No ORCHARD_AGENT is set unless the caller asks for one: the enforcement hook must
    recognise the lease that created THIS worktree without being told who we are.
    """
    import os as _os

    env = {**_os.environ}
    env.pop("ORCHARD_AGENT", None)
    if agent:
        env["ORCHARD_AGENT"] = agent
    subprocess.run(["git", "-C", str(worktree), "add", "-A"], check=True, timeout=120)
    r = subprocess.run(
        ["git", "-C", str(worktree), "commit", "-qm", message],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    if r.returncode != 0:
        raise Fail(f"commit in {worktree} refused:\n{r.stderr[:600]}")


def run(sc: Scenario) -> None:
    sc.head("SCENARIO 5 — a two-phase project built end to end, entirely over MCP")
    sc.make_repo("taskmetrics", SCAFFOLD)
    alpha = McpClient(sc.repo, ROOT, agent="alpha")
    beta = McpClient(sc.repo, ROOT, agent="beta")

    try:
        # -- 1. bootstrap ---------------------------------------------------------
        sc.step("Agent ALPHA connects to a repository that has never seen Orchard")
        init = alpha.initialize()
        sc.check("the handshake succeeds", init["serverInfo"]["name"] == "orchard")
        sc.check(
            "the server tells an unadopted repo to run setup FIRST",
            "does not use Orchard yet" in init["instructions"]
            and "orchard_setup" in init["instructions"],
        )
        sc.check(
            "and does not nag a project that never asked for it",
            "If the user has not asked for this" in init["instructions"],
        )

        sc.step("ALPHA bootstraps the project — no shell, only MCP")
        out, code = alpha.tool("orchard_setup", agents="claude")
        sc.check("orchard_setup succeeded", code == 0, out[-300:])
        sc.check("it created the queue directory", (sc.repo / ".orchard").is_dir())
        sc.check(
            "it wrote the per-project instructions into AGENTS.md",
            "ORCHARD:BEGIN" in (sc.repo / "AGENTS.md").read_text(),
        )
        sc.check(
            "it installed the enforcement hook",
            (sc.repo / ".git" / "hooks" / "pre-commit").exists(),
        )
        sc.git("add", "-A")
        sc.git("-c", "user.email=a@b", "-c", "user.name=t", "commit", "-qm", "orchard: adopt")

        sc.step("ALPHA configures the project through MCP")
        alpha.tool(
            "orchard_configure", set="gate.unit_tests.command", value="python3 -m pytest tests/ -q"
        )
        alpha.tool("orchard_configure", set="enforce.commit_without_lease", value="block")
        detected, _ = alpha.tool("orchard_reviewers_detect", write=True)
        has_reviewer = "family" in detected
        sc.note(
            f"reviewer discovery: {detected.strip().splitlines()[0][:90]}"
            if has_reviewer
            else "no local model server found; the critic gate "
            "will report UNAVAILABLE, which is the point"
        )
        listed, _ = alpha.tool("orchard_reviewers_list")
        if has_reviewer:
            sc.check(
                "the discovered reviewer is registered and declares its family",
                "alibaba" in listed or "family" in listed or "critic" in listed,
                listed[:200],
            )

        # After a fresh setup the instructions must stop asking for what is now set.
        init2 = McpClient(sc.repo, ROOT, agent="probe")
        try:
            text = init2.initialize()["instructions"]
        finally:
            init2.close()
        sc.check(
            "the instructions now point at orchard_brief, not setup",
            "orchard_brief" in text and "does not use Orchard yet" not in text,
        )
        sc.check(
            "and no longer ask for a test command",
            "No test command is configured" not in text,
            text[-300:],
        )

        # -- 2. fill the queue ----------------------------------------------------
        sc.step("Build a two-phase queue with real dependencies")
        alpha.tool(
            "orchard_phase_add",
            id="P1",
            title="Core",
            body="Duration parsing, statistics, and a CLI over both.",
        )
        alpha.tool(
            "orchard_phase_add",
            id="P2",
            title="Reporting",
            needs="P1",
            body="Markdown report built on the core.",
        )
        for tid, title, needs, globs in [
            ("P1.T1", "duration parser", "", "taskmetrics/parse.py,tests/test_parse.py"),
            ("P1.T2", "summary statistics", "", "taskmetrics/stats.py,tests/test_stats.py"),
            ("P1.T3", "command line", "P1.T1,P1.T2", "taskmetrics/cli.py,tests/test_cli.py"),
            ("P2.T4", "markdown report", "P1", "taskmetrics/report.py,tests/test_report.py"),
        ]:
            alpha.tool(
                "orchard_task_add",
                id=tid,
                phase=tid.split(".")[0],
                title=title,
                needs=needs,
                globs=globs,
            )

        sc.step("The brief is what an agent reads instead of the rule files")
        brief, _ = alpha.tool("orchard_brief", phase="P1")
        sc.check("it names what is ready", "P1.T1" in brief and "P1.T2" in brief)
        sc.check("it explains what is blocked and why", "Blocked" in brief and "P1.T3" in brief)
        sc.check(
            "it stays inside its token budget",
            len(brief) // 4 <= 1400,
            f"{len(brief) // 4} approx tokens",
        )

        # -- 3. fan out -----------------------------------------------------------
        sc.step("Ask what may start — two independent tasks, one blocked")
        plan = alpha.jtool("orchard_next", phase="P1")
        ready = sorted(r["id"] for r in plan["ready"])
        sc.check("T1 and T2 are offered together", ready == ["P1.T1", "P1.T2"], str(ready))
        blocked = {b["item"]: b for b in plan["blocked"]}
        sc.check(
            "T3 is withheld on its dependencies",
            blocked["P1.T3"]["reason"] == "deps"
            and sorted(blocked["P1.T3"]["waiting_on"]) == ["P1.T1", "P1.T2"],
            json.dumps(blocked.get("P1.T3")),
        )
        sc.check(
            "the critical path is reported as two deep",
            len(plan["critical_path"]) >= 2,
            str(plan["critical_path"]),
        )

        sc.step("ALPHA and BETA claim one task each, simultaneously")
        a_claim = alpha.jtool(
            "orchard_claim", id="P1.T1", globs="taskmetrics/parse.py,tests/test_parse.py"
        )
        b_claim = beta.jtool(
            "orchard_claim", id="P1.T2", globs="taskmetrics/stats.py,tests/test_stats.py"
        )
        wt_a, wt_b = Path(a_claim["worktree"]), Path(b_claim["worktree"])
        sc.check("each agent got its own worktree", wt_a != wt_b)
        sc.check(
            "both are real git checkouts", (wt_a / ".git").exists() and (wt_b / ".git").exists()
        )
        sc.check(
            "the claim records the holder",
            a_claim["holder"] == "alpha" and b_claim["holder"] == "beta",
            f"{a_claim['holder']} / {b_claim['holder']}",
        )

        sc.step("A third agent tries to take a task whose files overlap ALPHA's")
        alpha.tool(
            "orchard_task_add",
            id="P1.T9",
            phase="P1",
            title="parser tweak",
            globs="taskmetrics/parse*.py",
        )
        gamma = McpClient(sc.repo, ROOT, agent="gamma")
        try:
            gamma.initialize()
            msg, code = gamma.tool("orchard_claim", id="P1.T9")
            sc.check(
                "the claim is refused (exit 3), not silently allowed",
                code == 3,
                f"exit {code}: {msg[:200]}",
            )
            sc.check(
                "the refusal names the overlap and the holder",
                "overlaps" in msg and "alpha" in msg,
                msg[:200],
            )
            sc.check(
                "the refusal names an alternative the agent could take instead",
                "could take instead" in msg or "P1.T" in msg,
                msg[:250],
            )
        finally:
            gamma.close()

        # -- 4. implement ---------------------------------------------------------
        sc.step("Both agents write real code in their own worktrees")
        sc.write("taskmetrics/parse.py", PARSE_PY, repo=wt_a)
        sc.write("tests/test_parse.py", PARSE_TEST, repo=wt_a)
        sc.write("taskmetrics/stats.py", STATS_PY, repo=wt_b)
        sc.write("tests/test_stats.py", STATS_TEST, repo=wt_b)

        sc.step("The enforcement hook refuses a commit ALPHA has not claimed")
        (wt_a / "taskmetrics" / "stats.py").write_text("# poaching beta's file\n")
        subprocess.run(
            ["git", "-C", str(wt_a), "add", "taskmetrics/stats.py"], check=True, timeout=120
        )
        r = subprocess.run(
            ["git", "-C", str(wt_a), "commit", "-m", "poach"],
            capture_output=True,
            text=True,
            env={**__import__("os").environ, "ORCHARD_AGENT": "alpha"},
            timeout=180,
        )
        sc.check("git refused the commit", r.returncode != 0)
        sc.check(
            "and said which agent holds the file",
            "beta" in r.stderr and "STOP" in r.stderr,
            r.stderr[:300],
        )
        subprocess.run(
            ["git", "-C", str(wt_a), "reset", "-q", "HEAD", "taskmetrics/stats.py"],
            check=True,
            timeout=120,
        )
        (wt_a / "taskmetrics" / "stats.py").unlink()

        sc.step("Each agent commits only what it holds — now allowed")
        _commit_in(sc, wt_a, "P1.T1: duration parser")
        _commit_in(sc, wt_b, "P1.T2: summary statistics")
        sc.check(
            "both worktrees have exactly one new commit",
            sc.git("rev-list", "--count", "main..HEAD", repo=wt_a) == "1"
            and sc.git("rev-list", "--count", "main..HEAD", repo=wt_b) == "1",
        )

        # -- 5. gates -------------------------------------------------------------
        sc.step("Run the real test suites through MCP")
        for agent, item in ((alpha, "P1.T1"), (beta, "P1.T2")):
            out, code = agent.tool("orchard_gate_run", id=item, gate="unit_tests")
            sc.check(
                f"{item}: the real pytest run passed",
                code == 0 and "PASSED" in out.upper(),
                out[-400:],
            )
        status_a, _ = alpha.tool("orchard_gate_status", id="P1.T1")
        sc.check("the gate status shows unit_tests ticked", "[x] unit_tests" in status_a, status_a)

        sc.step("Run the configured cross-family critic on ALPHA's diff")
        review_out, review_code = alpha.tool(
            "orchard_review",
            id="P1.T1",
            intent="Parse human duration strings like '1h30m' into seconds, rejecting "
            "anything not fully consumed by the token pattern.",
        )
        if has_reviewer:
            sc.check(
                "the critic ran and recorded a verdict (0=clean, 1=findings, "
                "3=partial, 2=unavailable)",
                review_code in (0, 1, 2, 3),
                f"exit {review_code}",
            )
            sc.note(f"critic: {review_out.strip().splitlines()[-1][:110]}")
        else:
            sc.check(
                "with no reviewer configured it records UNAVAILABLE, not a pass",
                review_code == 2,
                review_out[:200],
            )
        st = alpha.jtool("orchard_show", id="P1.T1")
        critic = st["gates"].get("critic", {})
        sc.check(
            "whatever happened, the critic outcome is recorded honestly",
            critic.get("outcome") in ("passed", "failed", "partial", "unavailable"),
            json.dumps(critic)[:250],
        )

        sc.step("Completion is refused until the remaining gates are satisfied")
        msg, code = alpha.tool("orchard_complete", id="P1.T1", model="claude-opus-5")
        sc.check(
            "exit 3, with every unmet condition listed at once",
            code == 3,
            f"exit {code}: {msg[:200]}",
        )

        sc.step("Record the agent gates, then merge and complete")
        for agent, item, reviewer_model in (
            (alpha, "P1.T1", "qwen3-local"),
            (beta, "P1.T2", "gemini-2.5-pro"),
        ):
            for gate in ("research", "rules", "implement", "standards", "bug_hunt", "dedupe"):
                agent.tool(
                    "orchard_gate_record", id=item, gate=gate, outcome="passed", evidence="checked"
                )
            agent.tool(
                "orchard_gate_record",
                id=item,
                gate="rubber_duck",
                outcome="passed",
                evidence="no findings",
                model=reviewer_model,
            )
            out, code = agent.tool("orchard_merge", id=item)
            sc.check(f"{item} merged cleanly", code == 0, out[:300])
            out, code = agent.tool("orchard_complete", id=item, model="claude-opus-5")
            sc.check(f"{item} completed", code == 0, out[:300])

        sc.check(
            "both modules are on the main branch",
            "parse_duration" in sc.git("show", "main:taskmetrics/parse.py")
            and "summarise" in sc.git("show", "main:taskmetrics/stats.py"),
        )

        # -- 6. the dependent task unblocks --------------------------------------
        sc.step("T3 was blocked on T1 AND T2 — it must now be ready")
        plan = alpha.jtool("orchard_next", phase="P1")
        ready = sorted(r["id"] for r in plan["ready"])
        sc.check(
            "T3 became ready the moment its last dependency landed", "P1.T3" in ready, str(ready)
        )
        sc.note(
            "Nothing told Orchard to unblock it — readiness is computed from the "
            "log, so it cannot drift from what actually shipped."
        )

        sc.step("ALPHA builds the CLI on top of both modules")
        claim = alpha.jtool(
            "orchard_claim", id="P1.T3", globs="taskmetrics/cli.py,tests/test_cli.py"
        )
        wt_c = Path(claim["worktree"])
        sc.check(
            "the new worktree already contains its dependencies' code",
            (wt_c / "taskmetrics" / "parse.py").exists()
            and (wt_c / "taskmetrics" / "stats.py").exists(),
        )
        sc.write("taskmetrics/cli.py", CLI_PY, repo=wt_c)
        sc.write("tests/test_cli.py", CLI_TEST, repo=wt_c)
        _commit_in(sc, wt_c, "P1.T3: command line")
        out, code = alpha.tool("orchard_gate_run", id="P1.T3", gate="unit_tests")
        sc.check(
            "the WHOLE suite passes in the new worktree — all three modules",
            code == 0 and "PASSED" in out.upper(),
            out[-500:],
        )
        for gate in ("research", "rules", "implement", "standards", "bug_hunt", "dedupe"):
            alpha.tool(
                "orchard_gate_record", id="P1.T3", gate=gate, outcome="passed", evidence="ok"
            )
        alpha.tool(
            "orchard_gate_record",
            id="P1.T3",
            gate="rubber_duck",
            outcome="passed",
            evidence="checked",
            model="gemini-2.5-pro",
        )
        alpha.tool(
            "orchard_gate_record",
            id="P1.T3",
            gate="critic",
            outcome="unavailable",
            reason="endpoint busy with another review",
        )
        alpha.tool("orchard_merge", id="P1.T3")
        out, code = alpha.tool("orchard_complete", id="P1.T3", model="claude-opus-5")
        sc.check(
            "T3 completes, with the unavailable critic recorded as a GAP",
            code == 0 and "critic" in out,
            out[:300],
        )

        # -- 7. close the phase, unblock the next --------------------------------
        sc.step("P2 depends on P1 — it must still be blocked until P1 closes")
        plan = alpha.jtool("orchard_next", phase="P2")
        sc.check("P2's task is withheld while P1 is open", not plan["ready"], str(plan["ready"]))

        sc.step("The speculative task from step 8 is dropped, not left dangling")
        out, code = alpha.tool(
            "orchard_abandon", id="P1.T9", reason="the conflict probe did not become real work"
        )
        sc.check("it can be abandoned with a recorded reason", code == 0, out[:200])
        plan = alpha.jtool("orchard_next", phase="P1")
        sc.check(
            "and is not offered again, nor listed as blocked",
            "P1.T9" not in [r["id"] for r in plan["ready"]]
            and "P1.T9" not in [b["item"] for b in plan["blocked"]],
            str(plan),
        )

        sc.step("Close phase P1 through its own pipeline")
        alpha.tool(
            "orchard_gate_record",
            id="P1",
            gate="research",
            outcome="passed",
            evidence="scoped up front",
        )
        out, code = alpha.tool("orchard_gate_run", id="P1", gate="unit_tests")
        sc.check("the phase-level suite runs against the merged result", code in (0, 2), out[-300:])
        for gate in ("tasks", "bug_hunt", "dedupe", "live_test", "corrections", "merge"):
            alpha.tool(
                "orchard_gate_record", id="P1", gate=gate, outcome="passed", evidence="phase pass"
            )
        out, code = alpha.tool("orchard_complete", id="P1", model="claude-opus-5")
        sc.check("phase P1 completes once every task is done", code == 0, out[:300])

        sc.step("Now P2 unblocks")
        plan = alpha.jtool("orchard_next", phase="P2")
        sc.check(
            "P2.T4 became available when its phase dependency closed",
            [r["id"] for r in plan["ready"]] == ["P2.T4"],
            str(plan),
        )

        # -- 8. the record ---------------------------------------------------------
        sc.step("The live CLI actually works — a green suite is a different claim")
        r = subprocess.run(
            ["python3", "-m", "taskmetrics.cli", "1h30m", "45m"],
            cwd=str(sc.repo),
            capture_output=True,
            text=True,
            timeout=120,
        )
        sc.check("the merged CLI runs end to end", r.returncode == 0, r.stderr[:300])
        sc.check("and computes the right answer", "total  8100s" in r.stdout, r.stdout)

        sc.step("The board, the log, and the reconstruction all agree")
        board, _ = alpha.tool("orchard_board")
        sc.check(
            "the board shows P1 fully done",
            "3/3 tasks" in board or "4/4 tasks" in board,
            board[:600],
        )
        doctor, dcode = alpha.tool("orchard_doctor")
        sc.check("doctor reports a healthy log", dcode == 0, doctor[-400:])
        log = "\n".join(p.read_text() for p in (sc.repo / ".orchard" / "events").glob("*.jsonl"))
        sc.check(
            "the committed log carries NO absolute paths — it is portable to "
            "another checkout, a container, or a teammate",
            '"worktree":"/' not in log.replace(" ", ""),
        )
        sc.check(
            "every agent wrote its own shard, so two agents never conflict",
            len(list((sc.repo / ".orchard" / "events").glob("*.jsonl"))) >= 2,
            str([p.name for p in (sc.repo / ".orchard" / "events").glob("*")]),
        )

        sc.step("Nothing looped, and the work done is accounted for")
        loops, lcode = alpha.tool("orchard_loops")
        sc.check(
            "a healthy project reports NO loops (exit 2 = nothing to report)",
            lcode == 2,
            loops[:300],
        )
        prog = alpha.jtool("orchard_progress")
        by_item = {r["item"]: r for r in prog}
        sc.check(
            "every completed task records exactly one attempt and one commit",
            all(
                by_item[t]["attempts"] == 1 and by_item[t]["commits"] == 1
                for t in ("P1.T1", "P1.T2", "P1.T3")
            ),
            json.dumps([by_item[t] for t in ("P1.T1", "P1.T2", "P1.T3")]),
        )
        sc.check(
            "the two agents are recorded as the holders",
            by_item["P1.T1"]["holders"] == ["alpha"] and by_item["P1.T2"]["holders"] == ["beta"],
            json.dumps([by_item["P1.T1"], by_item["P1.T2"]]),
        )
        sc.check(
            "held time is measured to COMPLETION, not to now",
            all(0 < by_item[t]["held_seconds"] < 3600 for t in ("P1.T1", "P1.T2")),
            json.dumps({t: by_item[t]["held_seconds"] for t in ("P1.T1", "P1.T2")}),
        )

        sc.step("A deliberately circular plan is caught before anyone works it")
        alpha.tool("orchard_phase_add", id="PX", title="circular")
        alpha.tool("orchard_task_add", id="X.A", phase="PX", needs="X.C", globs="x/a")
        alpha.tool("orchard_task_add", id="X.B", phase="PX", needs="X.A", globs="x/b")
        alpha.tool("orchard_task_add", id="X.C", phase="PX", needs="X.B", globs="x/c")
        loops, lcode = alpha.tool("orchard_loops")
        found = json.loads(loops)
        cyc = [f for f in found if f["kind"] == "dependency_cycle"]
        sc.check("the cycle is detected", cyc, loops[:300])
        sc.check(
            "and is BLOCKING — nothing in the ring can ever start",
            cyc[0]["severity"] == "block",
            json.dumps(cyc[0]),
        )
        sc.check(
            "the finding names the ring so it can be broken",
            "->" in cyc[0]["detail"],
            cyc[0]["detail"][:200],
        )
        nxt = alpha.jtool("orchard_next", phase="PX")
        sc.check(
            "and the scheduler offers none of the ring",
            not nxt["ready"] and len(nxt["cycles"]) == 1,
            json.dumps(nxt)[:300],
        )
        for t in ("X.A", "X.B", "X.C"):
            alpha.tool("orchard_remove", id=t, reason="circular plan, rewritten")
        alpha.tool("orchard_remove", id="PX", reason="circular plan, rewritten")

        sc.step("The whole project reconstructs from the log alone")
        _, _ = alpha.tool("orchard_board")
        replay = subprocess.run(
            ["python3", "-m", "orchard", "--repo", str(sc.repo), "replay"],
            capture_output=True,
            text=True,
            timeout=300,
            env={**__import__("os").environ, "PYTHONPATH": str(ROOT)},
        )
        sc.check("replay produces the reconstruction brief", replay.returncode == 0)
        for fragment in ("P1.T1", "P1.T3", "Duration parsing", "Reporting"):
            sc.check(f"it carries {fragment!r}", fragment in replay.stdout, replay.stdout[:300])
    finally:
        alpha.close()
        beta.close()
