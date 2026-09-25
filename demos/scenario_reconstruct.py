"""Scenario 3 — everything is destroyed except the log, and the project is rebuilt.

The strongest claim ddflow makes: *if you lose the source, the database, the boards
and the worktrees, the event log alone still contains everything needed to rebuild.*

The scenario builds a small project while logging operator prompts, research verdicts
and lessons, then deletes the entire repository except `.ddflow/events/`, and checks
that what comes back is genuinely sufficient — not merely non-empty.

It is explicit about what is NOT claimed: the source is not reproduced byte-for-byte.
Model outputs are not deterministic, so what replay reconstructs is the *decision
history* — every instruction, every rejected alternative, every rule learned. That is
the part no other artefact holds.
"""

from __future__ import annotations

import shutil

from harness import Scenario

SCAFFOLD = {"README.md": "# ledger\n", ".gitignore": "__pycache__/\n"}


def run(sc: Scenario) -> None:
    sc.head("SCENARIO 3 — reconstruct the project from the event log alone")
    repo = sc.make_repo("ledger", SCAFFOLD)
    sc.ddflow("init")

    sc.step("A working session: prompts, research, a rejected approach, a lesson")
    sid = sc.jddflow("session", "start", "--model", "claude-opus-5", "--tool", "claude-code")[
        "session"
    ]
    prompts = [
        "Build a double-entry ledger. Every transaction must balance to zero; "
        "reject any that does not, at write time rather than in a nightly job.",
        "Use integer minor units everywhere. I have been burned by float rounding "
        "in a financial system before and will not accept it again.",
        "Add an append-only audit trail. Entries are never updated or deleted; a "
        "correction is a new compensating entry.",
    ]
    for pr in prompts:
        sc.ddflow("session", "prompt", sid, "--text", pr, quiet=True)
    sc.ddflow(
        "research",
        "--id",
        "R1",
        "--question",
        "Should amounts be stored as Decimal or as integer minor units?",
        "--claim",
        "integer minor units are safer and faster",
        "--falsifier",
        "a benchmark showing Decimal within 10% and no rounding drift",
        "--probe",
        "python3 bench_money.py",
        "--probe-output",
        "Decimal: 3.4s / 1e6 ops; int: 0.21s; drift(Decimal)=0",
        "--verdict",
        "CONFIRMED",
        "--sources",
        "https://peps.python.org/pep-0327/",
    )
    sc.ddflow(
        "research",
        "--id",
        "R2",
        "--question",
        "Should we store a running balance column for speed?",
        "--claim",
        "a denormalised running balance is worth the risk",
        "--falsifier",
        "any concurrent-write test producing an inconsistent balance",
        "--probe",
        "python3 probe_concurrent_balance.py",
        "--probe-output",
        "12 writers: 3 inconsistent balances out of 500 txns",
        "--verdict",
        "REFUTED",
    )
    sc.ddflow(
        "lesson",
        "add",
        "--id",
        "L1",
        "--title",
        "Never store a denormalised balance in a ledger",
        "--rule",
        "Derive the balance by summing entries; a stored balance drifts under concurrent writes.",
        "--why",
        "Measured 3 inconsistent balances in 500 concurrent transactions.",
        "--how",
        "If reads are slow, add a materialised snapshot with the entry id "
        "it was computed at — never a mutable running total.",
        "--tags",
        "data-integrity,concurrency",
    )
    sc.ddflow("phase", "add", "P1", "--title", "Ledger core")
    sc.ddflow(
        "task",
        "add",
        "P1.T1",
        "--phase",
        "P1",
        "--title",
        "Entry model",
        "--globs",
        "ledger/entry.py",
    )
    sc.ddflow("session", "end", sid, "--summary", "designed the ledger core")

    sc.step("Capture what the log knows, then destroy everything else")
    before = sc.ddflow("replay")[1]
    events_dir = repo / ".ddflow" / "events"
    backup = sc.dir.parent / "SURVIVING_LOG_ledger"
    shutil.rmtree(backup, ignore_errors=True)
    shutil.copytree(events_dir, backup)
    log_bytes = sum(f.stat().st_size for f in backup.rglob("*.jsonl"))
    sc.note(f"The entire surviving artefact is {log_bytes} bytes of JSONL.")
    shutil.rmtree(repo)
    sc.check("the repository is gone", not repo.exists())

    sc.step("Rebuild a bare repository and restore ONLY the log")
    sc.make_repo("ledger", {"README.md": "# recovered\n"})
    shutil.rmtree(repo / ".ddflow", ignore_errors=True)
    (repo / ".ddflow").mkdir(parents=True)
    shutil.copytree(backup, repo / ".ddflow" / "events")
    sc.check(
        "no index, no config, no source — only events/",
        not (repo / ".ddflow" / "index.db").exists()
        and list((repo / ".ddflow" / "events").glob("*.jsonl")),
    )

    sc.step("Re-derive everything from the log")
    _, out, _ = sc.ddflow("rebuild")
    sc.check("the index rebuilt from the log with no other input", "rebuilt index" in out, out)
    after = sc.ddflow("replay")[1]
    sc.check(
        "the reconstruction brief is byte-identical to the pre-destruction one",
        after == before,
        f"lengths {len(before)} vs {len(after)}",
    )

    sc.step("Check the brief is actually SUFFICIENT, not merely non-empty")
    for fragment, why in [
        ("must balance to zero", "the core business rule"),
        ("integer minor units", "the operator's explicit constraint"),
        ("burned by float rounding", "the REASON behind that constraint"),
        ("compensating entry", "the audit-trail design"),
        ("Never store a denormalised balance", "the lesson learned"),
        ("already tried and rejected", "the rejected-approaches section"),
        ("denormalised running balance", "the specific rejected approach"),
        ("3 inconsistent balances out of 500", "the probe output that killed it"),
        ("P1.T1", "the work queue's shape"),
    ]:
        sc.check(f"the brief carries {why}", fragment in after, f"missing fragment: {fragment!r}")

    sc.step("The queue state came back too, not just the prose")
    board = sc.ddflow("board")[1]
    sc.check("the work queue rebuilt", "P1.T1" in board and "Ledger core" in board, board[:300])
    ready = [r["id"] for r in sc.jddflow("next", "--phase", "P1")["ready"]]
    sc.check("and it is schedulable again immediately", ready == ["P1.T1"], str(ready))

    sc.step("State what is NOT claimed")
    sc.check(
        "the brief warns that source is not reproduced byte-for-byte",
        "byte-for-byte" in after and "not deterministic" in after,
    )
    sc.note(
        "Replay reconstructs the decisions, not the bytes. That is the honest "
        "claim, and the document says so itself so no reader over-trusts it."
    )
