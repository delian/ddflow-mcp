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

import contextlib
import json
from pathlib import Path

from ..infra import git as G
from ..infra import worktree as W
from ..infra.fsio import LockTimeout, file_lock

EVENTS = ".ddflow/events"
#: Out-of-line gate evidence (B-gap-log-scale): committed with the shards that name it.
BLOBS = ".ddflow/blobs"
#: How long a door waits for another door's event-log commit before it leaves its own for the
#: next door: under the 30 s an append waits, so one slow hook never blocks a writer.
COMMIT_WAIT_S = 20.0


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


def commit_shards(
    repo: Path, reason: str, template: str = "events: {reason}", *, hold=None
) -> tuple[str, str]:
    """(commit sha, note): the sha when shards were committed; the note says why not, or
    which shards were left out as half-written ("" when there was nothing to say).

    ``hold`` is a context manager factory (the log's `transaction`). Under it the shards are
    read ONCE, each checked to hold whole lines, and written into a private index; the
    `git commit` of that index -- and the project's hooks it runs, which can take minutes
    (a pre-commit suite) -- happens outside it. The commit is therefore of exactly the bytes
    that were checked, whatever a writer appends meanwhile (that line waits for the next
    door), and no other ddflow command waits for the hooks: holding the append lock that long
    made every one of them time out with a "wedged agent" message
    (merge-holds-event-lock-during-ci).

    Two doors would still move HEAD together, so the commit and the resetting of the shared
    index sit under a lock of their own (in the git directory) that only event-log commits
    wait for, `COMMIT_WAIT_S` at most: a door that cannot get it leaves its lines, committed
    by whichever door comes next."""
    repo = Path(repo)
    hold = hold or contextlib.nullcontext
    git_dir = W.git(repo, "rev-parse", "--absolute-git-dir")
    held = contextlib.ExitStack()
    private = None
    if git_dir.ok and git_dir.out.strip():
        private = Path(git_dir.out.strip()) / "ddflow-eventcommit.index"
        try:  # only the wait for another door's commit is this note's; the append lock's is not
            held.enter_context(
                file_lock(Path(git_dir.out.strip()) / "ddflow-eventcommit.lock", COMMIT_WAIT_S)
            )
        except LockTimeout:
            return (
                "",
                f"event log not committed: another event-log commit ran for over {COMMIT_WAIT_S}s",
            )
    with held:
        if private is None:
            return "", "event log not committed: not a git repository"
        try:
            with hold():
                early, message, paths, skipped = _snapshot(repo, reason, template, private)
            if early is not None:
                return early
            env = {"GIT_INDEX_FILE": str(private)}
            done = W.git(repo, "commit", "-q", "-m", message, env=env)
            if not done.ok:
                return "", f"event log not committed: {(done.err or done.out).strip()[-300:]}"
            # The shared index still holds these paths as they were before the commit.
            W.git(repo, "reset", "-q", "--", *paths)
            return W.rev(repo, "HEAD"), skipped
        finally:
            private.unlink(missing_ok=True)


def _snapshot(repo: Path, reason: str, template: str, index: Path):
    """(early answer, message, the paths committed, the note): the shards and blobs read once,
    the whole-line shards written into the private ``index`` on top of HEAD. The early answer
    is the finished ``("", why)`` when there is nothing to commit or it must not be, else
    None."""

    def stop(why: str):
        return ("", why), "", [], ""

    listed = uncommitted_shards(repo)
    if listed is None:
        return stop("event log not committed: git could not list .ddflow/events")
    taken: dict[str, bytes] = {}
    left: list[str] = []
    for rel in listed:
        data = _read_whole(repo, rel)
        if data is None:
            left.append(rel)
        else:
            taken[rel] = data
    skipped = f"left out, being written or unreadable: {', '.join(left)}" if left else ""
    for rel in uncommitted_blobs(repo):
        with contextlib.suppress(OSError):
            taken[rel] = (repo / rel).read_bytes()
    if not taken:
        return stop(f"event log not committed: {skipped}" if skipped else "")
    busy = _busy(repo)
    if busy:
        return stop(f"event log not committed: {busy}")
    try:
        message = template.format(reason=reason)
    except (KeyError, IndexError, ValueError):
        message = f"events: {reason}"
    env = {"GIT_INDEX_FILE": str(index)}
    has_head = W.git(repo, "rev-parse", "-q", "--verify", "HEAD").ok
    loaded = W.git(repo, "read-tree", *(["HEAD"] if has_head else ["--empty"]), env=env)
    if not loaded.ok:
        return stop(f"event log not committed: git read-tree failed: {loaded.err[-200:]}")
    for rel, data in taken.items():
        blob = W.git(repo, "hash-object", "-w", "--stdin", input=data)
        if not blob.ok:
            return stop(f"event log not committed: git could not store {rel}")
        spec = f"100644,{blob.out.strip()},{rel}"
        if not W.git(repo, "update-index", "--add", "--cacheinfo", spec, env=env).ok:
            return stop(f"event log not committed: git could not stage {rel}")
    return None, message, sorted(taken), skipped


def _read_whole(repo: Path, rel: str) -> bytes | None:
    """The shard's bytes when every line parses as a JSON object; None when it does not (a
    writer is mid-line) or cannot be read."""
    try:
        data = (Path(repo) / rel).read_bytes()
        text = data.decode("utf-8")
        ok = all(not ln.strip() or isinstance(json.loads(ln), dict) for ln in text.splitlines())
    except (OSError, ValueError):
        return None
    return data if ok else None


def commit_at(log, cfg, door: str, reason: str) -> dict[str, str]:
    """`[log].commit_events` at one door (merge, complete, release, stop): the outcome
    fields to report. Never raises and never fails the caller: a refusal is a note."""
    if not cfg.log.commit_events or door not in cfg.log.commit_events_at:
        return {}
    try:
        # The append lock is held for the "whole" check and the staging only; the commit's
        # hooks run outside it (see `commit_shards`).
        sha, note = commit_shards(
            log.root, reason, cfg.log.commit_events_message, hold=log.transaction
        )
    except Exception as e:
        return {"events_note": f"event log not committed: {e}"}
    return {**({"events_committed": sha} if sha else {}), **({"events_note": note} if note else {})}
