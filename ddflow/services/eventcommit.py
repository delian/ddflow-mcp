"""Commit the event log: ddflow's own shards, nothing else (Bcd3512c891).

The log is the source of truth and is committed, but committing it was left to each
agent, and every claim writes a shard of its own in the primary checkout while the work
is committed in a worktree. Nothing committed those shards, so a clone or a pull got an
incomplete history. `commit_shards` is called after merge, complete and release.

It commits ONLY `.ddflow/events/*.jsonl`, with a pathspec so anything else staged in the
primary stays staged and untouched, never bypasses the repository's hooks, and never
fails the operation that called it: a refusal is returned as text for the caller to
report.
"""

from __future__ import annotations

from pathlib import Path

from ..infra import worktree as W

EVENTS = ".ddflow/events"


def uncommitted_shards(repo: Path) -> list[str]:
    """Event shards with changes git has not committed (new or appended)."""
    r = W.git(repo, "status", "--porcelain", "--untracked-files=all", "--", EVENTS)
    if not r.ok:
        return []
    return sorted(
        line[3:].strip() for line in r.out.splitlines() if line[3:].strip().endswith(".jsonl")
    )


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


def commit_shards(repo: Path, reason: str) -> tuple[str, str]:
    """(commit sha, "") when shards were committed; ("", why not) otherwise ("" when
    there was nothing to commit)."""
    repo = Path(repo)
    shards = uncommitted_shards(repo)
    if not shards:
        return "", ""
    busy = _busy(repo)
    if busy:
        return "", f"event log not committed: {busy}"
    add = W.git(repo, "add", "--", *shards)
    if not add.ok:
        return "", f"event log not committed: git add failed: {(add.err or add.out)[-200:]}"
    done = W.git(repo, "commit", "-q", "-m", f"events: {reason}", "--", *shards)
    if not done.ok:
        return "", f"event log not committed: {(done.err or done.out).strip()[-300:]}"
    return W.rev(repo, "HEAD"), ""
