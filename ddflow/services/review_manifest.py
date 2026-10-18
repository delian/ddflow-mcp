"""A manifest of the WHOLE change, carried by every review chunk.

A diff is reviewed one chunk at a time, so a reviewer raised findings the rest of the
change answered ('no templates, no README rows' while the templates were in another
chunk; 'nothing adds the guard' on a chunk that only deleted a shard). Each chunk's
prompt now lists every changed file with its added/removed lines and a one-line summary,
marks the files this chunk holds, names the new files and the files left out of review
([review].exclude_globs), so a fact that lives in a sibling chunk can be checked.
"""

from __future__ import annotations

import re

from ..core import unidiff

#: Names a one-line summary lists at most.
_NAMES = 4
_DEF_RE = re.compile(r"^\+\s*(?:async\s+)?(?:def|class)\s+(\w+)")
_HEAD_RE = re.compile(r"^\+(#{1,4})\s+(.{1,60})$")


def _summary(fd: unidiff.FileDiff, section: str) -> str:
    if fd.old is None:
        kind = "new file"
    elif fd.new is None:
        kind = "deleted"
    elif fd.old != fd.new:
        kind = f"renamed from {fd.old}"
    else:
        kind = "modified"
    names: list[str] = []
    for ln in section.split("\n"):
        m = _DEF_RE.match(ln) or _HEAD_RE.match(ln)
        if m:
            names.append(m.group(m.lastindex).strip())
    shown = ", ".join(list(dict.fromkeys(names))[:_NAMES])
    return f"{kind}; adds {shown}" if shown else kind


def _counts(section: str) -> tuple[int, int]:
    body = section.split("\n@@", 1)[1] if "\n@@" in section else ""
    lines = body.split("\n")
    return (
        sum(1 for ln in lines if ln.startswith("+") and not ln.startswith("+++")),
        sum(1 for ln in lines if ln.startswith("-") and not ln.startswith("---")),
    )


def build(
    diff: str,
    excluded: list[str] | None = None,
    max_files: int = 40,
) -> str:
    """The manifest text for ``diff`` (the whole change, as sent to the reviewers);
    "" when it has no files. ``max_files`` caps the rows (0 = no manifest); the rest are
    counted, never silently dropped."""
    if max_files <= 0:
        return ""
    rows = [(unidiff.parse_section(s), s) for s in unidiff.sections(diff)]
    if not rows:
        return ""
    out = [f"Change: {len(rows)} file(s) in all, sent to you in separate parts."]
    for fd, sec in rows[:max_files]:
        add, rem = _counts(sec)
        out.append(f"- {fd.path} (+{add} -{rem}): {_summary(fd, sec)}")
    if len(rows) > max_files:
        out.append(f"- ... and {len(rows) - max_files} more file(s)")
    new = [fd.path for fd, _s in rows if fd.old is None]
    out.append("New files: " + (", ".join(new) if new else "none"))
    if excluded:
        out.append(
            "Left out of review (generated or pinned, [review].exclude_globs): "
            + ", ".join(excluded)
        )
    return "\n".join(out)


def held(chunk: str) -> str:
    """The files this chunk's diff holds, for the prompt's 'this part' line."""
    return ", ".join(dict.fromkeys(fd.path for fd in unidiff.files(chunk)))
