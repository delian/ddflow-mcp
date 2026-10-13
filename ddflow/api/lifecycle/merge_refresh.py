"""`merge`'s document refresh, split out of merge.py to keep it under its size ceiling."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...infra import worktree as W
from ...services.export import refresh as RF


def _refresh_documents(repo: Path, cfg, wt: W.Worktree) -> dict[str, Any]:
    """`[export].refresh = merge`: regenerate the selected whole-file documents into the
    item's branch and commit them there, so they land in the merge. Never fatal: a document
    that cannot be refreshed is reported (``problems``) and the merge goes ahead.

    Empty when nothing is selected for ``merge`` (the default: refresh is off).
    """

    r = RF.refresh_selected(repo, "merge", root=wt.path, cfg=cfg)
    if not r.outcomes:
        return {}
    data = r.data()
    paths = r.changed
    if paths:
        add = W.git(wt.path, "add", "--", *paths)
        commit = (
            W.git(
                wt.path,
                "commit",
                "-q",
                "--no-verify",
                "-m",
                "refresh generated documents",
                "--",
                *paths,
            )
            if add.ok
            else add
        )
        if not commit.ok:
            W.git(wt.path, "reset", "-q", "HEAD", "--", *paths)
            created = {o.path for o in r.outcomes if o.action == "created"}
            for rel in paths:
                if rel in created:  # absent before the refresh: nothing to restore, remove it
                    (wt.path / rel).unlink(missing_ok=True)
                elif W.git(wt.path, "cat-file", "-e", f"HEAD:{rel}").ok:
                    W.git(wt.path, "checkout", "--", rel)
            data["changed"] = []
            data["problems_note"] = (
                f"could not commit the refreshed documents: {commit.err or commit.out}"
            )
    data["summary"] = r.summary() + (
        f"; {data['problems_note']}" if "problems_note" in data else ""
    )
    return data
