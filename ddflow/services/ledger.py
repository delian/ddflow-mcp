"""The completion ledger: what a task REQUIRED, what was DONE, and what changed after.

`item.completed` carries a `ledger` of git facts that exist nowhere else in the log (files
the landing commit changed, which of them are tests, a digest of the requirement text).
Everything else is rebuilt from the log itself: the requirement as the item stood when it
was completed, every gate's outcome including skips and their reasons, and each later
`task.updated` as an amendment -- recorded, never overwritten. A completion that predates
the ledger is rebuilt from the log alone and says so (`reconstructed`).

Nothing here copies diffs or text: digests, short lists and counts, so it stays cheap to
store and to show. Judging a ledger against the repository is the next task (B-verify-check).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..core.events import Event
from ..core.model import Item, fold

MAX_FILES = 100
_TEST = re.compile(r"(^|/)(tests?|spec)/|(^|/)test_[^/]*$|_test\.[a-z]+$|\.(test|spec)\.[a-z]+$")


def requirement_digest(title: str, body: str, globs: Sequence[str]) -> str:
    """12 hex digits over what the task asked for: its title, body and declared files."""
    text = "\x00".join([title, body, *sorted(globs)])
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:12]


def item_digest(it: Item) -> str:
    return requirement_digest(it.title, it.body, it.globs)


def git_facts(repo: Path, sha: str, it: Item) -> dict[str, Any]:
    """The ledger written at completion: requirement digest + what the landing changed.

    `files` is empty (and `files_known` false) when there is no sha or git could not list
    it: a missing fact is recorded as missing, never as "no files changed".
    """
    from ..infra.worktree import git_paths

    facts: dict[str, Any] = {"req": item_digest(it), "files_known": False}
    if not sha:
        return facts
    # `git_paths` reads -z bytes: plain `--name-only` C-quotes a non-ASCII name into text
    # that names no file, and the log cannot be corrected afterwards.
    listed = git_paths(repo, "show", "--name-only", "--format=", "-m", "--first-parent", sha)
    if listed is None:
        return facts
    files = sorted({f for f in listed if f.strip()})
    facts.update(
        files_known=True,
        files_total=len(files),
        files=files[:MAX_FILES],
        tests=[f for f in files if _TEST.search(f)][:MAX_FILES],
    )
    return facts


def build(events: Sequence[Event], item_id: str) -> dict[str, Any] | None:
    """The ledger of one completed item, or None when it was never completed."""
    idx = max(
        (i for i, ev in enumerate(events) if ev.subject == item_id and ev.kind == "item.completed"),
        default=-1,
    )
    if idx < 0:
        return None
    done = events[idx]
    it = fold(events[: idx + 1], strict=False).items.get(item_id)
    if it is None:
        return None
    stored = done.data.get("ledger") if isinstance(done.data.get("ledger"), dict) else {}
    gates = {
        g: {"outcome": r.outcome, **({"reason": r.reason[:200]} if r.reason else {})}
        for g, r in sorted(it.gates.items())
    }
    amendments = [
        {"at": ev.ts, "by": ev.agent, "fields": sorted(k for k in ev.data if k != "id")}
        for ev in events[idx + 1 :]
        if ev.subject == item_id and ev.kind.endswith(".updated")
    ]
    now = fold(events, strict=False).items.get(item_id)
    at_completion = stored.get("req") or item_digest(it)  # the folded item IS completion-time
    drifted = bool(now and item_digest(now) != at_completion)
    return {
        "completed_at": done.ts,
        "completed_by": done.agent,
        "sha": done.data.get("sha", ""),
        "forced": bool(done.data.get("forced")),
        "overridden": list(done.data.get("overridden") or []),
        "requirement": {
            "title": it.title,
            "digest": at_completion,
            "globs": list(it.globs),
            "needs": list(it.needs),
            "body_chars": len(it.body),
        },
        "done": {
            "files_known": bool(stored.get("files_known")),
            "files_total": stored.get("files_total", 0),
            "files": list(stored.get("files") or []),
            "tests": list(stored.get("tests") or []),
        },
        "gates": gates,
        "skipped": sorted(g for g, v in gates.items() if v["outcome"] == "skipped"),
        "changelog": done.data.get("changelog") or {},
        "amendments": amendments,
        "requirement_changed_after": drifted,
        "reconstructed": not stored,
    }


def summary(ledger: dict[str, Any], files_shown: int = 8) -> dict[str, Any]:
    """The compact form `show` carries: counts and the first few files, not the lists."""
    d = ledger["done"]
    return {
        "completed_at": ledger["completed_at"],
        "sha": ledger["sha"],
        "requirement": ledger["requirement"]["digest"],
        "files_known": d["files_known"],
        "files_total": d["files_total"],
        "files": d["files"][:files_shown],
        "tests": len(d["tests"]),
        "skipped_gates": ledger["skipped"],
        "forced": ledger["forced"],
        "amendments": len(ledger["amendments"]),
        "requirement_changed_after": ledger["requirement_changed_after"],
        "reconstructed": ledger["reconstructed"],
    }
