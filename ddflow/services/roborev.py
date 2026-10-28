"""Which agent roborev actually reviewed a commit with (B03437a6b45), and the WAIT that
polls one job to its verdict (DX-roborev-wait-no-review).

`roborev review <sha>` prints the agent it ENQUEUED the job for (`agent: kilo`). When
that agent fails -- here, a kilo whose config file it can no longer parse -- the daemon
falls back to its `backup_agent` and the review is written by that one instead
(`roborev show N` says "Review for job N (by claude-code)"). The standards gate was
recorded with whatever `--model` the agent typed, so a same-family reviewer passed as a
cross-family one. The job record roborev keeps names the agent that ran it; this reads it.

`roborev show --job N` of an unfinished job says "no review found" -- the wording of a job
that never existed -- and `roborev wait --job N` prints no live/failed distinction either.
`roborev list --json` is the ONE output that always carries the job's real status, so
`wait_job` polls that: queued/running reads as still running (waitable, never failed),
done as a finished review, failed/canceled as ended (re-enqueue it), and a job the list
no longer has as vanished. `python -m ddflow.services.roborev wait --job N` is the
command form; it exits 0 reviewed clean, 1 findings, 2 still running / could not tell.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic, sleep

from ..infra import proc

#: How many of the branch's most recent jobs are searched for the reviewed commit.
#: The shortest abbreviated sha matched as a prefix (git's own default abbreviation).
_MIN_ABBREV = 7
_LIMIT = 100
_TIMEOUT_S = 30
#: The job types that are reviews of commits: one commit (`review`), or a range
#: (`range`, what `roborev review --since <base>` records -- Bf4a6ce1b06). Fix, refine
#: and other agent jobs are not reviews.
_REVIEW_JOBS = ("review", "range")


@dataclass
class Review:
    """The newest finished roborev review of a commit."""

    job: int
    agent: str
    model: str = ""

    @property
    def reviewer(self) -> str:
        """The name to judge the reviewer's family by: its model when roborev records
        one (kilo drives other providers' models), else the agent itself."""
        return self.model or self.agent


#: Job states that will still become a review; failed/canceled never will.
_UNFINISHED = ("queued", "running")

#: Seconds between two `wait_job` polls of `roborev list --json`.
_POLL_S = 5.0
#: The smallest cap an ask ever runs with: honouring a bound whose remainder is near zero
#: must not become an INSTANT timeout of a healthy ask (which would read as unjudged); the
#: floor's price is that one hung ask can run this long past the bound, never two whole
#: ask timeouts.
_FLOOR_S = 0.05


def _covers(git_ref: str, sha: str) -> bool:
    """A job of ``sha`` itself, or of a range ending at it (`roborev review --since`).
    roborev records full shas; an abbreviated one (7+ hex) is matched as a prefix."""
    end = git_ref.rsplit("..", 1)[-1].strip().lower()
    return (
        len(end) >= _MIN_ABBREV
        and all(c in "0123456789abcdef" for c in end)
        and sha.startswith(end)
    )


def _unfinished_note(jobs: list, sha: str) -> str:
    """Why a covering review job is not a review: still queued/running (wait for it), or
    ended failed/canceled (re-run it). '' when no review job covers ``sha``."""
    covering = [
        j for j in jobs
        if isinstance(j, dict) and j.get("job_type", "review") in _REVIEW_JOBS
        and _covers(str(j.get("git_ref") or ""), sha) and _job_id(j) is not None
    ]  # fmt: skip
    live = [j for j in covering if j.get("status") in _UNFINISHED]
    pool = live or covering
    if not pool:
        return ""
    # `roborev show --job N` of an unfinished job says "no review found", which reads as a
    # vanished job (B08da521f70); the job is there, just not done.
    j = max(pool, key=lambda x: _job_id(x) or 0)
    if not live:
        return (
            f"roborev job {_job_id(j)} for {sha[:10]} ended {j.get('status') or 'unfinished'}, "
            f"so it holds no review of it: re-run `roborev review {sha[:10]}`; "
            "the reviewer is unchecked"
        )
    return (
        f"roborev job {_job_id(j)} for {sha[:10]} is {j.get('status')}, not done: "
        f"`roborev wait --job {_job_id(j)}` first, or let ddflow poll its status: "
        f"`python -m ddflow.services.roborev wait --job {_job_id(j)}` (ddflow's wait reads "
        "the job list, which says running vs failed where `roborev show --job` of an "
        "unfinished job says 'no review found'); the reviewer is unchecked"
    )


def _no_review_note(jobs: list, sha: str) -> str:
    """Why ``sha`` has no finished review: an unfinished or ended job, or none in the list."""
    if note := _unfinished_note(jobs, sha):
        return note
    where_ = f"among its last {_LIMIT} jobs" if len(jobs) >= _LIMIT else "on this branch"
    return f"roborev holds no finished review of {sha[:10]} {where_}; the reviewer is unchecked"


def _read_list(exe: str, where: Path, timeout: float = _TIMEOUT_S) -> tuple[list, str]:
    """(the jobs, why it FAILED to read them): `roborev list --json` from ``where`` (the one
    source that carries a job's REAL status -- `show`/`wait` say 'no review found' for a job
    that is merely queued). `([], note)` when the ask failed: an empty list then reads as
    empty, never as a guess about a status it could not fetch. ``timeout`` caps the ask, so
    a poll with a near caller's bound cannot overshoot it by a whole read."""
    p = proc.capture([exe, "list", "--json", "--limit", str(_LIMIT)], cwd=where, timeout=timeout)
    if p.error is not None:
        return [], str(
            p.error
        ) or "an unspeakable error"  # truthy either way: `failed` doubles as the returned note
    if p.returncode != 0:
        why = (p.stderr or p.stdout).strip().splitlines()[-1:] or [f"exit {p.returncode}"]
        return [], why[0][:160]
    if not (p.stdout or "").strip():
        # exit 0 with nothing printed is a read that said nothing: never an empty job list
        return [], "roborev list returned no JSON"
    try:
        jobs = json.loads(p.stdout or "[]")
    except ValueError:
        return [], "roborev list returned no JSON"
    if jobs is None:  # `null` for a repository roborev has no jobs for
        return [], ""
    if not isinstance(jobs, list):
        return [], f"roborev list returned no job list ({str(jobs)[:80]})"
    cleaned = [j for j in jobs if isinstance(j, dict)]
    if not cleaned and jobs:
        return [], "roborev list returned no job records"
    return cleaned, ""


def review_of(where: Path, sha: str) -> tuple[Review | None, str]:
    """(the newest finished review of ``sha``, a note). ``(None, note)`` when roborev is
    not installed, could not be asked, or holds no finished review of ``sha``. Searched from ``where`` (the item's
    worktree), whose branch is the one roborev filed the job under."""
    exe = shutil.which("roborev")
    if not exe:
        return None, f"roborev is not on PATH; the reviewer of {sha[:10]} is unchecked"
    jobs, failed = _read_list(exe, where)
    if failed:
        return None, f"could not ask roborev which agent reviewed {sha[:10]} ({failed})"
    done = [
        (_job_id(j), j)
        for j in jobs if isinstance(j, dict)
        and j.get("status") == "done"
        and j.get("job_type", "review") in _REVIEW_JOBS
        and _covers(str(j.get("git_ref") or ""), sha)
        and _job_id(j) is not None
    ]  # fmt: skip
    if not done:
        return None, _no_review_note(jobs, sha)
    job, j = max(done, key=lambda pair: pair[0])
    return Review(job=job, agent=str(j.get("agent") or ""), model=str(j.get("model") or "")), ""


def _job_id(j: dict) -> int | None:
    try:
        return int(j.get("id"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


@dataclass
class WaitResult:
    """What a bounded wait for one roborev job knows. ``review`` is set only when the job
    FINISHED; ``live`` names a job still queued/running at the bound -- RUNNING, never
    failed (it may still finish; wait again); both unset names a job that ended without a
    review or a list that could not be read. ``text`` is the review text ("" when none)."""

    review: Review | None = None
    note: str = ""
    text: str = ""
    live: bool = False


def wait_job(
    where: Path,
    job: int,
    wait_s: int,
    *,
    on_tick: Callable[[], None] | None = None,
) -> WaitResult:
    """Wait up to ``wait_s`` seconds for job ``job`` to end, POLLING `roborev list --json`
    every ``_POLL_S`` -- never `roborev wait`/`show`, whose 'no review found' for an
    unfinished job is the wording of a job that never existed. The poll's own words rule:
    queued/running = still running (a progress line, then keep waiting, ticking ``on_tick``
    -- the caller's lease keeper -- each poll); done = the finished review (its verdict read
    with `roborev wait --job` and its text with `roborev show --job`, both truthful once
    done); failed/canceled = ended (re-enqueue); no longer listed = vanished (re-enqueue);
    the bound with the job still live = ``live``. ``wait_s`` <= 0 polls once and returns."""
    import sys

    exe = shutil.which("roborev")
    if not exe:
        return WaitResult(note=f"roborev is not on PATH; job {job} is unchecked")
    started = monotonic()
    deadline = started + max(0, wait_s)
    live_state = (
        "queued or running"  # the last status read (the bound-check reads it between polls)
    )
    while True:
        if wait_s > 0 and monotonic() >= deadline:
            # the bound lapsed since the last read, which named the job live: say so NOW.
            # The read after this check is a whole ask in itself, so checking only after it
            # would overshoot the bound by one slow read.
            return WaitResult(live=True, note=_live(job, live_state, monotonic() - started, where))
        cap = _TIMEOUT_S if wait_s <= 0 else max(_FLOOR_S, min(_TIMEOUT_S, deadline - monotonic()))
        jobs, failed = _read_list(exe, where, cap)
        if failed:
            return WaitResult(
                note=f"roborev's job list could not be read ({failed}): job {job} is unchecked"
            )
        rec = next((j for j in jobs if _job_id(j) == job), None)
        if rec is None:
            return WaitResult(
                note=(
                    f"roborev holds no job {job} in this repository: the review for it was "
                    f"never written ('no review found' would be the very same words); re-run "
                    f"`roborev review <sha of the branch head>` to enqueue it, then wait again"
                )
            )
        status = str(rec.get("status") or "")
        if status == "done":
            if on_tick is not None:
                on_tick()  # the verdict asks below can outlive the bound: renew the lease first
            # The two verdict asks SHARE the remaining bound, floored at _FLOOR_S: even a
            # hanging `wait`/`show` on a done job cannot run past the bound by more than
            # twice the floor, never by two whole ask timeouts.
            allowance = (deadline - monotonic()) if wait_s > 0 else _TIMEOUT_S
            return _done(exe, where, job, rec, allowance)
        if status not in _UNFINISHED:
            return WaitResult(
                note=(
                    f"roborev job {job} ended {status}: it will never carry a review; re-run "
                    f"`roborev review <sha of the branch head>`; the reviewer is unchecked"
                )
            )
        elapsed = monotonic() - started
        if elapsed >= wait_s:  # wait_s <= 0: this is the read's own bound running out
            return WaitResult(live=True, note=_live(job, status, elapsed, where))
        print(f"roborev job {job}: still {status} after {round(elapsed)}s", file=sys.stderr)
        if on_tick is not None:
            on_tick()  # the caller's lease keeper: a long wait outlives an unrenewed lease
        left = (deadline - monotonic()) if wait_s > 0 else _POLL_S
        if left <= 0:
            # the progress print or the tick ate the rest of the bound: report the job LIVE
            # now. `sleep` of a negative span raises, and an over-long tick must read as
            # running, never crash the wait.
            return WaitResult(live=True, note=_live(job, status, monotonic() - started, where))
        live_state = status
        sleep(min(_POLL_S, left))


def _live(job: int, status: str, elapsed: float, where: Path | None = None) -> str:
    """Why the wait ends with the job still queued/running: it is RUNNING, never failed.
    The one sure way forward: wait again (from the same repository) once the queue drained,
    or raise the bound."""
    rerun = f"`python -m ddflow.services.roborev wait --job {job}"
    if where is not None:
        rerun += f" --repo {where}"
    return (
        f"roborev job {job} is still {status} after {round(elapsed)}s "
        f"(running, not failed: it may still finish); {rerun}` again later, "
        f"or raise [gates].standards_wait_s"
    )


def _done(exe: str, where: Path, job: int, rec: dict, timeout: float = _TIMEOUT_S) -> WaitResult:
    """The job is done, so its verdict IS readable: `roborev wait --job` exits 0 clean and 1
    on findings, and `roborev show --job` prints the review. A verdict that cannot be read
    is unjudged, so its text (if any) is never presented as a clean pass or as findings: an
    exit roborev never means by 1 (a signal, an unknown code) is unjudged too. The two asks
    share the one ``timeout`` allowance: `show` gets the tail after `wait` (floored at
    ``_FLOOR_S``), so a done job whose verdict asks both hang cannot run two allowances."""
    rv = Review(job=job, agent=str(rec.get("agent") or ""), model=str(rec.get("model") or ""))
    cap = max(_FLOOR_S, timeout)
    t0 = monotonic()
    w = proc.capture([exe, "wait", "--job", str(job)], cwd=where, timeout=cap)
    spent = monotonic() - t0
    shown = proc.capture(
        [exe, "show", "--job", str(job)], cwd=where, timeout=max(_FLOOR_S, cap - spent)
    )
    text = shown.stdout if shown.error is None and shown.returncode == 0 else ""
    if w.error is not None or w.timed_out or w.returncode not in (0, 1):
        # `why` names the REAL trouble. A timeout IS an error (`Captured.timed_out` is
        # exactly `isinstance(error, TimeoutExpired)`), so `str(w.error)` already says it;
        # the exit code is the fallback.
        why = str(w.error) if w.error is not None else f"exit {w.returncode}"
        return WaitResult(
            note=(
                f"roborev job {job} is done but `roborev wait --job {job}` gave no verdict "
                f"({why}): the text below, if any, is unjudged"
            ),
            text=text,
        )
    if w.returncode == 1:
        return WaitResult(rv, f"roborev wait exited {w.returncode}", text)
    return WaitResult(rv, "", text)


def main(argv: list | None = None) -> int:
    """`python -m ddflow.services.roborev wait --job N [--wait-s S] [--repo .]`: the bounded
    poll of one enqueued job. `--wait-s` defaults to `[gates].standards_wait_s`; 0 (or a
    negative, which `wait_job` reads as <= 0) polls once. Exit 0 reviewed clean, 1 findings,
    2 still running at the bound / ended without a review / could not tell (never a pass)."""
    import argparse

    ap = argparse.ArgumentParser(prog="roborev", description=__doc__.partition("\n")[0])
    ap.add_argument(
        "wait", nargs="?"
    )  # the 'wait' in `python -m ddflow.services.roborev wait --job N`
    ap.add_argument("--job", type=int, required=True)
    ap.add_argument("--wait-s", type=int, default=None)
    ap.add_argument("--repo", default=".")
    a = ap.parse_args(argv)
    if a.wait not in (None, "wait"):
        ap.error(f"unknown command {a.wait!r} -- this entry point only knows `wait`")
    where = Path(a.repo).resolve()
    if a.wait_s is None:
        # A NEGATIVE value stays negative: wait_job reads anything <= 0 as one poll, exactly
        # as its contract says -- replacing a caller's -1 with the default would quietly turn
        # a poll-once into a 900s wait.
        # Imported here, not at the top (deferred: this module loads to READ a review too).
        from ..config import Config

        a.wait_s = Config.load(where).gates.standards_wait_s
    out = wait_job(where, a.job, a.wait_s)
    if out.note:
        print(f"roborev: {out.note}")
    if out.review is not None:
        print(f"reviewer: {out.review.reviewer} (the model roborev ran job {out.review.job} with)")
    if out.text:
        print(out.text)
    return 2 if out.review is None else (1 if out.note else 0)


if __name__ == "__main__":
    import sys as _sys

    _sys.exit(main())
