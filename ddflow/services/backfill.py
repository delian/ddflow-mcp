"""Find the landing of a completion that carries no commit, so it can be verified anyway.

A completion from before the ledger recorded no files and often no sha. The commit is
still findable: the log's `merged_sha` for the item, or a commit on the integration branch
whose subject names the item the way `ddflow merge` writes it ("merge B22: ...", "B187:
..."). What this returns is RECONSTRUCTED evidence -- found after the fact by a weak
identity -- and `verify` says so; it is never written into the log, so it can never be
mistaken for what the completing agent recorded.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..core.model import State
from . import ledger as LG


def _is_commit(repo: Path, sha: str) -> bool:
    from ..infra.worktree import git

    return bool(sha) and git(repo, "cat-file", "-e", f"{sha}^{{commit}}", timeout=60).ok


def find_commit(repo: Path, st: State, item_id: str) -> tuple[str, str] | None:
    """(sha, how it was found) for an item's landing, or None."""
    from ..infra.worktree import default_branch, git

    it = st.items.get(item_id)
    if it is not None and _is_commit(repo, it.merged_sha):
        return it.merged_sha, "the merge sha the log recorded"
    head = re.compile(
        rf"^(merge )?{re.escape(item_id)}:"
    )  # how `ddflow merge` and fix commits name it
    r = git(
        repo,
        "log",
        "--format=%H%x09%s",
        "-E",
        f"--grep=(merge )?{re.escape(item_id)}",
        default_branch(repo),
        timeout=60,
    )
    if r.ok:
        for line in r.out.splitlines():
            sha, _, subject = line.partition("\t")
            if head.match(subject):
                return sha, f"a commit whose subject names {item_id}"
    return None


def apply(repo: Path, st: State, led: dict[str, Any], item_id: str) -> dict[str, Any]:
    """Fill a reconstructed ledger's missing landing from git, marking where it came from."""
    if not led["reconstructed"] or led["done"]["files_known"]:
        return led
    # A recorded sha that is a real commit is the landing; only otherwise search. The file
    # list must come from the SAME commit the ledger names.
    found = (
        (led["sha"], "the sha the completion recorded") if _is_commit(repo, led["sha"]) else None
    )
    found = found or find_commit(repo, st, item_id)
    it = st.items.get(item_id)
    if found is None or it is None:
        return led
    sha, how = found
    facts = LG.git_facts(repo, sha, it)
    if not facts.get("files_known"):
        return led
    return {
        **led,
        "sha": sha,
        "done": {
            "files_known": True,
            "files_total": facts["files_total"],
            "files": facts["files"],
            "tests": facts["tests"],
        },
        "backfill": {"sha": sha, "how": how},
    }
