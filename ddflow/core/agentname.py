"""What an agent name may contain -- ONE rule, for every surface that accepts one.

A declared name becomes a log SHARD FILENAME, so one with a path separator would write
outside the events directory and one with a newline would corrupt the line-oriented log.
The MCP surface (`ddflow_identify`, `as_agent`, `_meta`) and the harness declaration both
refuse such a name; they used to each carry their own copy of the pattern.

`fullmatch`, and no anchors. With `^...$` and `.match()` a pattern accepts `"reviewer" plus a newline`
(Python's `$` matches before a final newline), so the guarantee would live in whatever
happened to strip the name first rather than in the check credited with it.

The CLI's `--agent` and `DDFLOW_AGENT` are deliberately NOT checked with it today: they are
the operator's own words and the shard filename is sanitised separately
(`infra.log.EventLog.shard`).
"""

from __future__ import annotations

import re

AGENT_NAME = re.compile(r"[A-Za-z0-9._-]{1,64}")

#: The tail of every refusal, so the surfaces word it alike.
USABLE = "use letters, digits, '.', '_' or '-', up to 64 characters."


def is_valid(name: str) -> bool:
    """Is ``name`` usable as an agent name (exactly: no surrounding whitespace)?"""
    return AGENT_NAME.fullmatch(name) is not None


def refusal(name: object) -> str:
    """The sentence that says ``name`` cannot be an agent name."""
    return f"{name!r} is not a usable agent name: {USABLE}"
