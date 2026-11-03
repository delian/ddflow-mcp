"""Scenario 6 shared helpers: the per-task work cycle and the pytest runner."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: The second agent, so act 1 can complete its handshake before act 2 uses it.
BETA: list = [None]


def work(sc, client, repo, wt_root, item, files, *, author="claude-opus-5"):
    """Claim, write, test, pass the pipeline, complete, merge. The per-task order."""
    claim = client.jtool("ddflow_claim", id=item)
    wt = Path(claim["worktree"])
    sc.check(f"{item} got its own worktree", wt.is_dir() and wt.parent == wt_root, str(wt))

    brief = client.tool("ddflow_brief", item=item)[0]
    # The brief fences a governing decision as `id="D1"`: match that token, not the
    # bare substring, so a future `D10` decision cannot satisfy (or trip) the check.
    # The assertion is both directions on purpose: the scenario is load-bearing on
    # "scoping by globs, not by broadcast", so D1 must appear exactly for the files
    # it governs (ledger/core/money.py, ledger/report/**) and not otherwise.
    governs_d1 = any(
        rel == "ledger/core/money.py" or rel.startswith("ledger/report/") for rel in files
    )
    sc.check(
        f"{item}'s brief surfaces decision D1 exactly where it governs {item}'s files",
        ('id="D1"' in brief) == governs_d1,
        brief[:400],
    )

    for rel, body in files.items():
        p = wt / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body.lstrip(), "utf-8")
    sc.commit_in(wt, f"{item}: implement")

    # research -> rules -> implement, then the review stack, then the tests.
    client.tool(
        "ddflow_gate_record",
        id=item,
        gate="research",
        outcome="passed",
        evidence=f"probed: the {item} API shape against a worked example",
    )
    client.tool(
        "ddflow_gate_record",
        id=item,
        gate="rules",
        outcome="passed",
        evidence="ddflow_brief read; D1 applies",
    )
    client.tool("ddflow_gate_record", id=item, gate="implement", outcome="passed")
    client.tool(
        "ddflow_gate_record",
        id=item,
        gate="rubber_duck",
        outcome="passed",
        evidence="a different family tried to refute it and could not",
        model="qwen3-coder",
    )
    client.tool(
        "ddflow_gate_record",
        id=item,
        gate="critic",
        outcome="passed",
        evidence="diff vs intent: agree",
        model="gemini-2.5-pro",
    )
    client.tool(
        "ddflow_gate_record",
        id=item,
        gate="standards",
        outcome="unavailable",
        reason="roborev is not installed on this machine",
    )

    code, out = run_pytest(wt)
    client.tool(
        "ddflow_gate_record",
        id=item,
        gate="unit_tests",
        outcome="passed" if code == 0 else "failed",
        command="python -m pytest -q",
        exit_code=str(code),
        evidence=out[-400:],
    )
    sc.check(f"{item}'s real tests really passed", code == 0, out[-800:])

    client.tool(
        "ddflow_gate_record",
        id=item,
        gate="bug_hunt",
        outcome="passed",
        evidence="swept: off-by-one, empty-collection, silent knob drop",
    )
    client.tool(
        "ddflow_gate_record",
        id=item,
        gate="dedupe",
        outcome="passed",
        evidence="grepped the operation, not the name; nothing pre-existing",
    )
    client.tool("ddflow_gate_record", id=item, gate="merge", outcome="passed")

    merged = client.jtool("ddflow_merge", id=item)
    done, code = client.tool("ddflow_complete", id=item, model=author)
    sc.check(f"{item} completes once its whole pipeline has an outcome", code == 0, done)
    return merged


def run_pytest(wt: Path) -> tuple[int, str]:
    p = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=wt,
        capture_output=True,
        text=True,
        timeout=300,
    )
    return p.returncode, (p.stdout + p.stderr)
