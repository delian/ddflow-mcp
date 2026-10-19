"""The standards gate's roborev run on the item's COMBINED branch diff (B37468487fb).

`roborev review --branch --wait --local` gave no result in 200 s on a loaded shared host:
it reviewed commit by commit and blocked with no bound. This enqueues ONE range job over
the whole branch (`roborev review --since <merge-base>`), waits for it a configurable
`[gates].standards_wait_s`, and says WHY when it gives nothing: a timeout names the job
(left running; `roborev wait --job <id>` later) and the host, so the gate is recorded
`unavailable` with a reason rather than silently. Exit 0 reviewed clean, 1 findings,
2 could not run / no result in time (never a pass).

    python -m ddflow.services.roborev_branch [--base main] [--wait-s N]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..infra import proc
from ..infra import worktree as W
from . import roborev as RR


@dataclass
class Result:
    outcome: str  #: "clean" | "findings" | "unavailable"
    reason: str = ""
    job: int | None = None
    sha: str = ""
    text: str = ""

    @property
    def exit(self) -> int:
        return {"clean": 0, "findings": 1}.get(self.outcome, 2)


def _git(where: Path, *argv: str) -> str:
    r = W.git(where, *argv)
    return r.out.strip() if r.code == 0 else ""


def _find_job(exe: str, where: Path, sha: str, enqueue_out: str) -> int | None:
    m = re.search(r"\bjob\s*[#:=]?\s*(\d+)\b", enqueue_out, re.IGNORECASE)
    if m:
        return int(m.group(1))
    p = proc.capture([exe, "list", "--json", "--limit", "50"], cwd=where, timeout=RR._TIMEOUT_S)
    try:
        jobs = json.loads(p.stdout or "[]") or []
    except ValueError:
        return None
    ids = [
        int(j["id"]) for j in jobs
        if isinstance(j, dict) and RR._covers(str(j.get("git_ref") or ""), sha) and "id" in j
    ]  # fmt: skip
    return max(ids) if ids else None


def _job_status(exe: str, where: Path, job: int) -> str:
    """The roborev job's own status (`done`, `failed`, ...), "" when it cannot be read."""
    p = proc.capture([exe, "list", "--json", "--limit", "100"], cwd=where, timeout=RR._TIMEOUT_S)
    try:
        jobs = json.loads(p.stdout or "[]") or []
    except ValueError:
        return ""
    for j in jobs:
        if isinstance(j, dict) and str(j.get("id")) == str(job):
            return str(j.get("status") or "")
    return ""


def run(where: Path, base: str, wait_s: int) -> Result:
    exe = shutil.which("roborev")
    if not exe:
        return Result("unavailable", "roborev is not on PATH")
    head = _git(where, "rev-parse", "HEAD")
    mb = _git(where, "merge-base", base, "HEAD")
    if not head or not mb:
        return Result("unavailable", f"no merge-base of {base!r} and HEAD in {where}")
    if mb == head:
        return Result("unavailable", f"the branch has no commit ahead of {base!r}", sha=head)
    q = proc.capture([exe, "review", "--since", mb], cwd=where, timeout=RR._TIMEOUT_S)
    if q.error is not None or q.returncode != 0:
        why = q.error or (q.stderr or q.stdout).strip()[-200:] or f"exit {q.returncode}"
        return Result("unavailable", f"roborev could not enqueue the range review: {why}", sha=head)
    job = _find_job(exe, where, head, q.stdout + q.stderr)
    if job is None:
        return Result("unavailable", "roborev enqueued the review but named no job", sha=head)
    if wait_s <= 0:
        return Result(
            "unavailable",
            f"job {job} enqueued, not waited for: `roborev wait --job {job}`",
            job,
            head,
        )
    w = proc.capture([exe, "wait", "--job", str(job)], cwd=where, timeout=wait_s)
    if w.timed_out:
        return Result(
            "unavailable",
            f"roborev job {job} gave no result in {wait_s}s (the host is loaded?); it is still "
            f"queued or running: `roborev wait --job {job}`, or raise [gates].standards_wait_s",
            job, head,
        )  # fmt: skip
    shown = proc.capture([exe, "show", "--job", str(job)], cwd=where, timeout=RR._TIMEOUT_S)
    text = shown.stdout if shown.error is None and shown.returncode == 0 else ""
    if w.returncode == 0:
        return Result("clean", job=job, sha=head, text=text)
    # `roborev wait` exits 1 on a FAIL verdict AND on a job error: the job's status tells which.
    status = _job_status(exe, where, job)
    if status == "done":
        return Result("findings", f"roborev wait exited {w.returncode}", job, head, text)
    return Result(
        "unavailable",
        f"roborev job {job} ended {status or 'in an unknown state (its status could not be read; the text below, if any, is unjudged)'} with no review (wait exit {w.returncode})",
        job, head, text,
    )  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="roborev_branch", description=__doc__.split("\n")[0])
    ap.add_argument("--base", default="")
    ap.add_argument("--wait-s", type=int, default=None)
    ap.add_argument("--repo", default=".")
    a = ap.parse_args(argv)
    where = Path(a.repo).resolve()
    wait_s, base = a.wait_s, a.base
    if wait_s is None or not base:
        cfg = Config.load(where)
        wait_s = cfg.gates.standards_wait_s if wait_s is None else wait_s
        base = base or cfg.worktree.base_ref or W.default_branch(W.repo_root(where))
    r = run(where, base, wait_s)
    print(f"standards: {r.outcome}" + (f" -- {r.reason}" if r.reason else ""))
    if r.sha:
        print(f"reviewed_sha: {r.sha} (record with `gate record --reviewed-sha {r.sha}`)")
    if r.job is not None:
        print(f"roborev job: {r.job}")
    if r.text:
        print(r.text)
    return r.exit


if __name__ == "__main__":
    sys.exit(main())
