"""Yield-requested and breakable leases surface in `ddflow doctor` and `ddflow status`
(DX-lease-yield-doctor-status).

Before this item, a contended lease that shows no progress was told only to the holder
(brief, heartbeat) and to the claimant. The operator surfaces — `doctor` and `status`
status — must list it too, with how long it has been idle ("regression test: ddflow
doctor and ddflow status list a yield-requested lease and say how long it has been
idle")."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core import clock

A, B = "agent-a", "agent-b"
IDLE, AGO = 300, 1000  # asked after IDLE s idle; breakable after AGO s


def _backdate(repo, item: str, seconds: float) -> None:
    """Make ``item``'s lease look ``seconds`` older by rewriting the time the log records
    for it (add, claim and start), so no test depends on how long a subprocess takes to
    start (test_lease_progress_breakable.py)."""
    for f in (repo / ".ddflow" / "events").glob("*.jsonl"):
        out, hit = [], False
        for line in f.read_text().splitlines():
            e = json.loads(line)
            if e.get("subject") == item and e.get("kind") != "lease.renewed":
                if "at" in e.get("data", {}):
                    e["data"]["at"] -= seconds
                e["ts"] = clock.iso_at(clock.epoch(e["ts"]) - seconds)
                hit = True
            out.append(json.dumps(e, separators=(",", ":")) if hit else line)
        if hit:
            f.write_text("\n".join(out) + "\n")


def _setup(repo) -> None:
    assert run_cli(repo, "init", agent=A)[0] == 0
    run_cli(repo, "config", "lease.hold_max_s", str(AGO), agent=A)
    run_cli(repo, "config", "lease.idle_max_s", str(IDLE), agent=A)
    for item in ("T1", "T2"):
        assert (
            run_cli(repo, "task", "add", item, "--title", item, "--globs", "src/a.py", agent=A)[0]
            == 0
        )
    assert run_cli(repo, "claim", "T1", "--no-worktree", agent=A)[0] == 0


def _idle_line(text: str, item: str) -> str:
    """The rendered yield line for ``item``, "" when absent. Bound to the yield_text
    signature sentence, on purpose: "T1" and "no progress for" appearing anywhere in the
    whole output, or on a health line, would pass while the yield surface stayed missing
    (review findings)."""
    for ln in text.splitlines():
        if "no progress for" in ln and f"waits for the files {item} holds" in ln:
            return ln
    return ""


def test_doctor_and_status_list_a_yield_requested_lease_with_its_idle_time(repo):
    _setup(repo)
    assert run_cli(repo, "claim", "T2", "--no-worktree", agent=B)[0] == 3  # records the wait
    _backdate(repo, "T1", AGO)  # idle AGO s >= hold_max_s: asked AND breakable
    code, out, err = run_cli(repo, "doctor", agent=A)
    assert code in (0, 1)
    line = _idle_line(out + err, "T1")
    assert line and clock.fmt_age(AGO) in line  # listed, with the idle time
    assert "can now be broken" in line  # breakable shown too (hold_max_s reached)
    code, out, err = run_cli(repo, "status", agent=A)
    assert code in (0, 1)
    assert "Yield requested:" in out + err  # the section exists
    line = _idle_line(out + err, "T1")
    assert line and clock.fmt_age(AGO) in line  # same lease, same idle time
    assert "can now be broken" in line  # breakable shown in status too


def test_doctor_quotes_the_remedy_for_a_stale_package_glob(repo):
    """The moved stale_glob_notes note is copy-pastable: the corrected globs are ONE
    shell-quoted argument. The space form forces quoting -- a plain 'stale p/' without
    quotes is two words, so the shlex.quote rendering is what the pin demands."""
    assert run_cli(repo, "init", agent=A)[0] == 0
    assert (
        run_cli(repo, "task", "add", "S1", "--title", "S1", "--globs", "stale p.py", agent=A)[0]
        == 0
    )
    (repo / "stale p").mkdir(parents=True)
    (repo / "stale p" / "__init__.py").write_text("")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    code, out, err = run_cli(repo, "doctor", agent=A)
    assert code in (0, 1)
    note = next(ln for ln in (out + err).splitlines() if "now the package" in ln)
    assert f"`ddflow update S1 --globs {shlex.quote('stale p/')}`" in note
