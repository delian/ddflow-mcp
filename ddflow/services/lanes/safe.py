"""Names, paths and ids from the log, made safe to print outside a fence."""

from __future__ import annotations

from ...core.provenance import clean

#: Longest value (a name, a path) printed outside a fence.
VALUE_CAP = 200


def safe(value: object) -> str:
    """``value`` on one line, without quotes, brackets or backticks (`provenance.clean`, the
    one definition of which characters those are), cut at `VALUE_CAP`: a lane that named
    itself with a heading or a tag cannot add structure to a prompt or a table. Cleaned per
    path segment, so a long path is kept whole. Free text goes in a fence instead."""
    return "/".join(clean(part) for part in str(value or "").split("/"))[:VALUE_CAP]
