"""Commit the event log: ddflow's own shards, nothing else (Bcd3512c891).

The log is the source of truth and is committed, but committing it was left to each
agent, and every claim writes a shard of its own in the primary checkout while the work
is committed in a worktree. Nothing committed those shards, so a clone or a pull got an
incomplete history. `commit_at` is called at the merge, complete, release and stop doors
(`[log].commit_events_at`).

It commits ONLY `.ddflow/events/*.jsonl`, with a pathspec so anything else staged in the
primary stays staged and untouched, never bypasses the repository's hooks, and never
fails the operation that called it: a refusal is returned as text for the caller to
report.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..infra import git as G
from ..infra import worktree as W

EVENTS = ".ddflow/events"
#: Out-of-line gate evidence (B-gap-log-scale): committed with the shards that name it.
BLOBS = ".ddflow/blobs"


def uncommitted_shards(repo: Path) -> list[str] | None:
    """Event shards with changes git has not committed (new or appended); None when git
    could not tell, which is not the same as "all committed".

    `-z`, so a path git would C-quote (a space, a non-ASCII byte) is read as it is."""
    entries = G.status(repo, EVENTS, untracked="all")
    if entries is None:
        return None
    return sorted(e.path for e in entries if e.path.endswith(".jsonl"))


def uncommitted_blobs(repo: Path) -> list[str]:
    """Blob files git has not committed (they are only ever created, never changed)."""
    entries = G.status(repo, BLOBS, untracked="all")
    return sorted(e.path for e in entries or [] if e.path.endswith(".json"))


def _busy(repo: Path) -> str:
    """Why the primary must not be committed to right now, or ""."""
    gd = W.git(repo, "rev-parse", "--absolute-git-dir")
    if not gd.ok:
        return "not a git repository"
    git_dir = Path(gd.out.strip())
    if (git_dir / "index.lock").exists():
        return "another git command holds the index"
    for marker in ("MERGE_HEAD", "REBASE_HEAD", "CHERRY_PICK_HEAD", "rebase-merge", "rebase-apply"):
        if (git_dir / marker).exists():
            return f"a {marker.lower().replace('_head', '')} is in progress"
    return ""


def whole(repo: Path, rel: str) -> bool:
    """True when every line of the shard parses as JSON: a shard another live process is
    in the middle of appending to ends in half a line, and committing that publishes a
    line nobody can read. Such a shard waits for the next door."""
    try:
        with open(Path(repo) / rel, encoding="utf-8") as f:
            return all(not ln.strip() or isinstance(json.loads(ln), dict) for ln in f)
    except (OSError, ValueError):
        return False


def commit_shards(repo: Path, reason: str, template: str = "events: {reason}") -> tuple[str, str]:
    """(commit sha, note): the sha when shards were committed; the note says why not, or
    which shards were left out as half-written ("" when there was nothing to say)."""
    repo = Path(repo)
    listed = uncommitted_shards(repo)
    if listed is None:
        return "", "event log not committed: git could not list .ddflow/events"
    shards = [s for s in listed if whole(repo, s)]
    left = [s for s in listed if s not in shards]
    skipped = f"left out, being written or unreadable: {', '.join(left)}" if left else ""
    blobs = uncommitted_blobs(repo)
    if not shards and not blobs:
        return "", f"event log not committed: {skipped}" if skipped else ""
    busy = _busy(repo)
    if busy:
        return "", f"event log not committed: {busy}"
    try:
        message = template.format(reason=reason)
    except (KeyError, IndexError, ValueError):
        message = f"events: {reason}"
    shards = [*shards, *blobs]
    add = W.git(repo, "add", "--", *shards)
    if not add.ok:
        return "", f"event log not committed: git add failed: {(add.err or add.out)[-200:]}"
    done = W.git(repo, "commit", "-q", "-m", message, "--", *shards)
    if not done.ok:
        return "", f"event log not committed: {(done.err or done.out).strip()[-300:]}"
    return W.rev(repo, "HEAD"), skipped


def commit_at(log, cfg, door: str, reason: str) -> dict[str, str]:
    """`[log].commit_events` at one door (merge, complete, release, stop): the outcome
    fields to report. Never raises and never fails the caller: a refusal is a note."""
    if not cfg.log.commit_events or door not in cfg.log.commit_events_at:
        return {}
    try:
        # The append lock held from the check to the commit: a ddflow writer cannot add a
        # line between the "whole" check and the snapshot `git commit -- <paths>` takes.
        with log.transaction():
            sha, note = commit_shards(log.root, reason, cfg.log.commit_events_message)
    except Exception as e:
        return {"events_note": f"event log not committed: {e}"}
    return {**({"events_committed": sha} if sha else {}), **({"events_note": note} if note else {})}
