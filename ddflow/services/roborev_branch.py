"""The standards gate's roborev run on the item's COMBINED branch diff (B37468487fb).

`roborev review --branch --wait --local` gave no result in 200 s on a loaded shared host:
it reviewed commit by commit and blocked with no bound. This enqueues ONE range job over
the whole branch (`roborev review --since <merge-base>`), waits for it a configurable
`[gates].standards_wait_s`, and says WHY when it gives nothing: a timeout names the job
(left running; `roborev wait --job <id>` later) and the host, so the gate is recorded
`unavailable` with a reason rather than silently. Exit 0 reviewed clean, 1 findings,
2 could not run / no result in time (never a pass).

    python -m ddflow.services.roborev_branch [--base main] [--wait-s N] [--job N]

The wait is queue-aware (DX-roborev-speed): a timeout reports the job's place in
roborev's queue ("position 3 of 6 queued, 1 running, ETA ~12 min"), and the result is
`unavailable` with a PENDING-RERUN marker naming the job. A sha that already has a
queued/running/done job is never enqueued again (its job is reused), so the re-run path
for a standards gate left `unavailable` is simply to run this again once the queue has
drained -- it collects the same job -- or `--job N` to collect that job by id.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..core import clock
from ..core.digest import content_digest
from ..infra import fsio, proc
from ..infra import worktree as W
from . import roborev as RR


@dataclass
class Result:
    outcome: str  #: "clean" | "findings" | "unavailable"
    reason: str = ""
    job: int | None = None
    sha: str = ""
    text: str = ""
    pending_rerun: bool = False  #: the job may still finish: collect it with --job N

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
    ids = [
        int(j["id"]) for j in _list(exe, where)
        if RR._covers(str(j.get("git_ref") or ""), sha) and "id" in j
    ]  # fmt: skip
    return max(ids) if ids else None


_LIVE = ("queued", "running")


def _list(exe: str, where: Path, limit: int = 100) -> list[dict]:
    p = proc.capture(
        [exe, "list", "--json", "--limit", str(limit)], cwd=where, timeout=RR._TIMEOUT_S
    )
    try:
        jobs = json.loads(p.stdout or "[]") or []
    except ValueError:
        return []
    return [j for j in jobs if isinstance(j, dict)] if isinstance(jobs, list) else []


def _reusable_job(jobs: list[dict], sha: str, mb: str) -> int | None:
    """The newest queued/running/done review job of exactly this work (the range from
    ``mb`` to it; a single-commit job is not the branch review), so one sha is never enqueued twice."""
    ids = []
    for j in jobs:
        ref = str(j.get("git_ref") or "")
        if j.get("job_type", "review") not in RR._REVIEW_JOBS or j.get("status") not in (
            *_LIVE,
            "done",
        ):
            continue
        if not RR._covers(ref, sha) or RR._job_id(j) is None:
            continue
        start = ref.rsplit("..", 1)[0].strip().rstrip(".").lower() if ".." in ref else ""
        if not (len(start) >= RR._MIN_ABBREV and mb.startswith(start)):
            continue  # a single commit, or a range from elsewhere, is not the branch review
        ids.append(RR._job_id(j))
    return max(ids) if ids else None  # type: ignore[type-var]


def _secs(a: str, b: str) -> float | None:
    try:
        fa = clock.parse_ts(a)
        fb = clock.parse_ts(b)
    except ValueError:
        return None
    return (fb - fa).total_seconds()


def queue_position(jobs: list[dict], job: int, workers: int = 1) -> str:
    """'position N of M queued, K running, ETA ~X min' for ``job``; '' if it is not live."""
    me = next((j for j in jobs if RR._job_id(j) == job), None)
    if me is None or me.get("status") not in _LIVE:
        return ""
    queued = sorted(
        i for j in jobs if j.get("status") == "queued" and (i := RR._job_id(j)) is not None
    )
    running = sum(1 for j in jobs if j.get("status") == "running")
    durs = sorted(
        d for j in jobs if j.get("status") == "done"
        and (d := _secs(str(j.get("started_at") or ""), str(j.get("finished_at") or ""))) is not None
        and d > 0
    )[:]  # fmt: skip
    median = durs[len(durs) // 2] if durs else 0.0
    if me.get("status") == "running":
        return f"running now ({running} running, {len(queued)} queued behind)"
    pos = queued.index(job) + 1
    out = f"position {pos} of {len(queued)} queued, {running} running"
    if median:
        waves = (pos - 1) // max(1, workers) + 1
        out += f", ETA ~{max(1, round(waves * median / 60))} min at {round(median / 60)} min/job"
    return out


def _workers(exe: str, where: Path) -> int:
    p = proc.capture([exe, "status"], cwd=where, timeout=RR._TIMEOUT_S)
    m = re.search(r"Workers:\s*\d+/(\d+)", p.stdout or "")
    return int(m.group(1)) if m else 1


def _job_status(exe: str, where: Path, job: int) -> str:
    """The roborev job's own status (`done`, `failed`, ...), "" when it cannot be read."""
    for j in _list(exe, where):
        if str(j.get("id")) == str(job):
            return str(j.get("status") or "")
    return ""


@contextlib.contextmanager
def _enqueue_lock(where: Path):
    """Serialise the list-then-enqueue of concurrent callers on one machine and repo."""
    key = content_digest(str(W.repo_root(where)), "sha1", length=12)
    with fsio.file_lock(fsio.temp_root() / f"ddflow-roborev-enqueue-{key}.lock"):
        yield


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
    with _enqueue_lock(where):  # list + enqueue is one step, or two callers both enqueue
        job = _reusable_job(_list(exe, where), head, mb)
        if job is None:
            q = proc.capture([exe, "review", "--since", mb], cwd=where, timeout=RR._TIMEOUT_S)
            if q.error is not None or q.returncode != 0:
                why = q.error or (q.stderr or q.stdout).strip()[-200:] or f"exit {q.returncode}"
                return Result(
                    "unavailable", f"roborev could not enqueue the range review: {why}", sha=head
                )
            job = _find_job(exe, where, head, q.stdout + q.stderr)
    return _collect(exe, where, job, head, wait_s)


def run_job(where: Path, job: int, wait_s: int) -> Result:
    """Collect an already-enqueued job by id (the re-run of a standards gate that was left
    `unavailable` with a pending job), without enqueueing anything."""
    exe = shutil.which("roborev")
    if not exe:
        return Result("unavailable", "roborev is not on PATH", job=job)
    me = next((j for j in _list(exe, where) if RR._job_id(j) == job), None)
    if me is None:
        return Result("unavailable", f"roborev has no job {job} in this repository", job=job)
    sha = str(me.get("git_ref") or "").rsplit("..", 1)[-1]
    return _collect(exe, where, job, sha, wait_s)


def _collect(exe: str, where: Path, job: int | None, head: str, wait_s: int) -> Result:
    if job is None:
        return Result("unavailable", "roborev enqueued the review but named no job", sha=head)
    where_in_queue = queue_position(_list(exe, where), job, _workers(exe, where))
    if where_in_queue:
        print(f"roborev job {job}: {where_in_queue}", file=sys.stderr)
    suffix = f" [{where_in_queue}]" if where_in_queue else ""
    rerun = f"PENDING-RERUN job {job}: collect it with `python -m ddflow.services.roborev_branch --job {job}`"
    if wait_s <= 0:
        return Result(
            "unavailable",
            f"job {job} enqueued, not waited for{suffix}: `roborev wait --job {job}`; {rerun}",
            job,
            head,
            pending_rerun=True,
        )
    w = proc.capture([exe, "wait", "--job", str(job)], cwd=where, timeout=wait_s)
    if w.timed_out:
        now = queue_position(_list(exe, where), job, _workers(exe, where))
        return Result(
            "unavailable",
            f"roborev job {job} gave no result in {wait_s}s (the host is loaded?)"
            f"{f' [{now}]' if now else ''}; it is still queued or running: "
            f"`roborev wait --job {job}`, or raise [gates].standards_wait_s. {rerun}",
            job, head, pending_rerun=True,
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
    ap.add_argument("--job", type=int, default=None, help="collect this existing job (re-run)")
    a = ap.parse_args(argv)
    where = Path(a.repo).resolve()
    wait_s, base = a.wait_s, a.base
    if wait_s is None or not base:
        cfg = Config.load(where)
        wait_s = cfg.gates.standards_wait_s if wait_s is None else wait_s
        base = base or cfg.worktree.base_ref or W.default_branch(W.repo_root(where))
    r = run_job(where, a.job, wait_s) if a.job is not None else run(where, base, wait_s)
    print(f"standards: {r.outcome}" + (f" -- {r.reason}" if r.reason else ""))
    if r.sha:
        print(f"reviewed_sha: {r.sha} (record with `gate record --reviewed-sha {r.sha}`)")
    if r.job is not None:
        print(f"roborev job: {r.job}")
    if r.pending_rerun:
        print(f"standards-pending-rerun: job {r.job}")
    if r.text:
        print(r.text)
    return r.exit


if __name__ == "__main__":
    sys.exit(main())
