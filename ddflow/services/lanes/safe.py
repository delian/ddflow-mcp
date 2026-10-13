"""Names, paths and ids from the log, made safe to print outside a fence."""

from __future__ import annotations

from ...core.provenance import clean

#: Longest value (a name, a path) printed outside a fence.
VALUE_CAP = 200
#: What `provenance.clean` keeps of one piece (its own cap).
CHUNK = 80


def safe(value: object) -> str:
    """``value`` on one line, without quotes, brackets or backticks (`provenance.clean`, the
    one definition of which characters those are), cut at `VALUE_CAP`: a lane that named
    itself with a heading or a tag cannot add structure to a prompt or a table. `clean` caps
    what it is given, so the text goes through it in pieces and a long path or branch is
    kept up to `VALUE_CAP`. Free text goes in a fence instead."""
    text = str(value or "")[:VALUE_CAP]
    return "".join(clean(text[i : i + CHUNK]) for i in range(0, len(text), CHUNK))
