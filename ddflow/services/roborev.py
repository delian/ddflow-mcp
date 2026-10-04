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
_LIMIT = 100
_TIMEOUT_S = 30


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
    """A job of ``sha`` itself, or of a range ending at it (`roborev review --since`)."""
    return bool(git_ref) and (git_ref == sha or git_ref.rsplit("..", 1)[-1] == sha)


def review_of(where: Path, sha: str) -> tuple[Review | None, str]:
    """(the newest finished review of ``sha``, a note). ``(None, "")`` when roborev is
    not installed -- nothing to check against; ``(None, note)`` when it could not be
    asked or holds no finished review of ``sha``. Searched from ``where`` (the item's
    worktree), whose branch is the one roborev filed the job under."""
    exe = shutil.which("roborev")
    if not exe:
        return None, ""
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
    done = [
        j
        for j in jobs if isinstance(j, dict)
        and j.get("status") == "done"
        and j.get("job_type", "review") == "review"
        and _covers(str(j.get("git_ref") or ""), sha)
    ]  # fmt: skip
    if not done:
        return None, f"roborev holds no finished review of {sha[:10]}; the reviewer is unchecked"
    j = max(done, key=lambda j: int(j.get("id") or 0))
    return Review(
        job=int(j["id"]), agent=str(j.get("agent") or ""), model=str(j.get("model") or "")
    ), ""
