"""Names, paths and ids from the log, made safe to print outside a fence."""

from __future__ import annotations

import re

_UNSAFE = re.compile(r"[\s\"'<>&`]+")
#: Longest metadata value (a name, a path) printed outside a fence.
VALUE_CAP = 200


def safe(value: object) -> str:
    """``value`` on one line, without quotes, brackets or backticks, cut at `VALUE_CAP`: a
    lane that named itself with a heading or a tag cannot add structure to a prompt or a
    table. Free text goes in a fence instead (`core.provenance.fence`)."""
    return _UNSAFE.sub(" ", str(value or "")).strip()[:VALUE_CAP]
