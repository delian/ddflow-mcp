"""Aider's instruction-file wiring: the one native surface where doing nothing is a
silent total failure rather than degraded behaviour."""

from __future__ import annotations

import re
from pathlib import Path

from ...infra.fsio import replace_text

#: The instruction file Aider must be told to load. One name, used by the writer and by
#: the checker, so "did we wire Aider up?" has one answer.
AIDER_READS = "AGENTS.md"


#: Does this text list `AGENTS.md` under `read:`, in EITHER YAML form?
#:
#: One pattern, used by the writer's idempotency guard and by `rules_status`. They used to
#: carry a line-anchored copy each while the writer could also emit an INLINE list, so
#: `adopt` produced `read: [CONVENTIONS.md, AGENTS.md]` -- correct YAML that both then
#: reported as not-binding. `doctor` failed a project that had just been adopted correctly,
#: and each re-adopt appended again, growing the file without bound. Found by roborev on
#: 3040d4b, reproduced end to end.
_AIDER_LISTED = re.compile(
    r"^\s*(?:-\s*)?AGENTS\.md\s*$"  # block form:  - AGENTS.md
    r"|^read:.*?\[[^\]]*\bAGENTS\.md\b[^\]]*\]",  # inline form: read: [x, AGENTS.md]
    re.M,
)


def _aider_lists_agents_md(text: str) -> bool:
    return bool(_AIDER_LISTED.search(text))


def _add_aider_read(path: Path) -> str:
    """Add `AGENTS.md` to `read:` in `.aider.conf.yml`.

    Aider discovers no instruction file at all -- not `AGENTS.md`, not a convention of its
    own -- so without this entry the rules are present in the repository and invisible to
    the agent. That makes this the one native surface where doing nothing is silent total
    failure rather than degraded behaviour.

    Edited as TEXT, not through a YAML round-trip: the file is the operator's, it may carry
    comments and ordering that matter to them, and a dump-and-rewrite would quietly discard
    both. `yaml` is also not a dependency of this package and should not become one to add
    a line.

    **Every form is normalised to the BLOCK form.** Writing an inline list back out was the
    source of three bugs at once: the reader did not recognise it, a trailing comment landed
    inside the brackets (`read: [CONVENTIONS.md]  # our docs, AGENTS.md]` -- unparseable,
    and the operator's own entry lost with it), and `read: []` became `read: [, AGENTS.md]`.
    One representation means the reader and the writer cannot disagree.
    """
    text = path.read_text("utf-8") if path.exists() else ""
    if _aider_lists_agents_md(text):
        return f"{path.name} already loads {AIDER_READS}"
    m = re.search(r"^read:([^\n]*)$", text, re.M)
    if m is None:
        prefix = text.rstrip() + "\n" if text.strip() else ""
        replace_text(path, f"{prefix}read:\n  - {AIDER_READS}\n")
        return f"{'added' if prefix else 'created'} read: {AIDER_READS} in {path.name}"

    existing, trailing = _aider_read_values(m.group(1))
    # Any following block-form entries belong to this key too, and must survive.
    rest = text[m.end() :]
    # Past the newline that ENDS the `read:` line, or the first iteration below sees "\n"
    # and stops before reading a single block entry -- which left the operator's entries in
    # the file but moved below ours, a reordering with no reason behind it.
    lead = len(rest) - len(rest.lstrip("\n"))
    rest = rest[lead:]
    consumed = 0
    for line in rest.splitlines(keepends=True):
        item = re.match(r"^\s+-\s*(.+?)\s*$", line)
        if not item:
            break
        existing.append(item.group(1))
        consumed += len(line)

    values = [*dict.fromkeys([*existing, AIDER_READS])]  # de-duplicated, order kept
    block = "read:" + trailing + "\n" + "".join(f"  - {v}\n" for v in values)
    replace_text(path, text[: m.start()] + block + rest[consumed:])
    return f"added {AIDER_READS} to read: in {path.name}"


def _aider_read_values(after_colon: str) -> tuple[list[str], str]:
    """`(values, trailing-comment)` for whatever followed `read:` on its own line.

    Handles the three forms an operator may have written -- an inline list, a bare scalar,
    or nothing (a block list follows) -- and keeps any trailing comment OUTSIDE the values,
    which is the bug that made `read: [x]  # note` unparseable when the comment was treated
    as part of the list.
    """
    raw = after_colon.strip()
    comment = ""
    bracket = re.match(r"^\[([^\]]*)\](.*)$", raw)
    if bracket:
        inner, comment = bracket.group(1), bracket.group(2)
        values = [v.strip().strip("'\"") for v in inner.split(",") if v.strip()]
        return values, (" " + comment.strip() if comment.strip() else "")
    if raw.startswith("#"):
        return [], " " + raw
    if "#" in raw:
        raw, comment = raw.split("#", 1)
        comment = " #" + comment
    raw = raw.strip()
    return ([raw] if raw else []), comment
