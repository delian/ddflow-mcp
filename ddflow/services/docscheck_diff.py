"""`ddflow docs check --diff BASE`: keep the surface the diff ADDED.

The extractors (``[[docs.extract]]``) list the whole public surface, so a project with a
long history reports everything it never documented. Against a base only the names whose
source line the diff added are the author's to document now; findings about the documents
themselves (a broken link, a stale name) are about the tree and are all kept.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..infra import git as G
from .docscheck_types import Finding

_HUNK = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@")

#: Path -> the added line numbers, or None when the whole file is new.
Added = dict[str, set[int] | None]


def added_lines(repo: Path | str, base: str) -> Added:
    """The lines of the work tree that differ from ``base`` (added or changed), plus every
    untracked file whole. Raises OSError when git cannot tell: a diff nobody could read is
    not an empty one."""
    done = G.run(
        repo,
        "-c",
        "core.quotepath=off",
        "diff",
        "--no-color",
        "--no-ext-diff",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        "-U0",
        base,
        "--",
    )
    if not done.ok:
        raise OSError(f"git diff {base} failed: {done.err.strip() or 'unknown base?'}")
    out: Added = {}
    path, header = "", False
    for line in done.out.splitlines():
        if line.startswith("diff --git "):  # a content line always starts with + - or a space
            header, path = True, ""
        elif header and line.startswith("+++ "):
            if line[4:].startswith('"'):
                raise OSError(f"git quotes a path in the diff of {base}; cannot read it")
            path = line[4:].removeprefix("b/") if line != "+++ /dev/null" else ""
        elif m := _HUNK.match(line):
            header = False
            if path:
                start, count = int(m.group(1)), int(m.group(2) or 1)
                out.setdefault(path, set()).update(range(start, start + count))  # type: ignore[union-attr]
    untracked = G.files(repo, "untracked")
    if untracked is None:
        raise OSError("git ls-files failed")
    out.update(dict.fromkeys(untracked))
    return out


def restrict(findings: list[Finding], docs: set[str], added: Added) -> list[Finding]:
    """``findings`` without the extractor ones (those outside ``docs``) the diff did not add."""
    keep = []
    for f in findings:
        if f.path in docs:
            keep.append(f)
        elif f.path in added and (added[f.path] is None or f.line in (added[f.path] or ())):
            keep.append(f)
    return keep
