"""Which agent roborev actually reviewed a commit with (B03437a6b45).

`roborev review <sha>` prints the agent it ENQUEUED the job for (`agent: kilo`). When
that agent fails -- here, a kilo whose config file it can no longer parse -- the daemon
falls back to its `backup_agent` and the review is written by that one instead
(`roborev show N` says "Review for job N (by claude-code)"). The standards gate was
recorded with whatever `--model` the agent typed, so a same-family reviewer passed as a
cross-family one. The job record roborev keeps names the agent that ran it; this reads it.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

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


def _covers(git_ref: str, sha: str) -> bool:
    """A job of ``sha`` itself, or of a range ending at it (`roborev review --since`).
    roborev records full shas; an abbreviated one (7+ hex) is matched as a prefix."""
    end = git_ref.rsplit("..", 1)[-1].strip().lower()
    return (
        len(end) >= _MIN_ABBREV
        and all(c in "0123456789abcdef" for c in end)
        and sha.startswith(end)
    )


def review_of(where: Path, sha: str) -> tuple[Review | None, str]:
    """(the newest finished review of ``sha``, a note). ``(None, note)`` when roborev is
    not installed, could not be asked, or holds no finished review of ``sha``. Searched from ``where`` (the item's
    worktree), whose branch is the one roborev filed the job under."""
    exe = shutil.which("roborev")
    if not exe:
        return None, f"roborev is not on PATH; the reviewer of {sha[:10]} is unchecked"
    try:
        p = proc.run(
            [exe, "list", "--json", "--limit", str(_LIMIT)],
            cwd=where,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
        )
    except (OSError, proc.SubprocessError) as exc:
        return None, f"could not ask roborev which agent reviewed {sha[:10]} ({exc})"
    if p.returncode != 0:
        why = (p.stderr or p.stdout).strip().splitlines()[-1:] or [f"exit {p.returncode}"]
        return None, f"could not ask roborev which agent reviewed {sha[:10]} ({why[0][:160]})"
    try:
        jobs = json.loads(p.stdout or "[]")
    except ValueError:
        return None, f"roborev list returned no JSON; the reviewer of {sha[:10]} is unchecked"
    if not isinstance(jobs, list):  # `null` for a repository roborev has no jobs for
        jobs = []
    done = [
        (_job_id(j), j)
        for j in jobs if isinstance(j, dict)
        and j.get("status") == "done"
        and j.get("job_type", "review") in _REVIEW_JOBS
        and _covers(str(j.get("git_ref") or ""), sha)
        and _job_id(j) is not None
    ]  # fmt: skip
    if not done:
        where_ = f"among its last {_LIMIT} jobs" if len(jobs) >= _LIMIT else "on this branch"
        return None, (
            f"roborev holds no finished review of {sha[:10]} {where_}; the reviewer is unchecked"
        )
    job, j = max(done, key=lambda pair: pair[0])
    return Review(job=job, agent=str(j.get("agent") or ""), model=str(j.get("model") or "")), ""


def _job_id(j: dict) -> int | None:
    try:
        return int(j.get("id"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
