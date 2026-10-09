"""Defaults both surfaces and the typed layer share, declared where a command declaration
(`surfaces/declared/`, which stays free of `ddflow.api`) can read them.

Each is re-exported by the module that owns the behaviour it sets, so the name callers use
does not change.
"""

from __future__ import annotations

#: Where an item sits when nobody says otherwise. The MIDDLE of the range, so a later
#: item can be pushed either way without renumbering anything.
#:
#: Declared once and imported by the parser, not written twice. Duplicated, it became
#: 100 in argparse and 0 in the api layer -- so every phase and task created over MCP was
#: filed at the TOP priority while the CLI filed them in the middle, and nothing said so.
DEFAULT_PRIORITY = 100

#: What `next` offers when nobody says otherwise. TASKS, because a phase is an umbrella
#: and "work on P1" is not an instruction anyone can act on.
#:
#: Defaulted in the typed layer as well as in argparse, and that duplication is the point:
#: `next_item` was first written with `kind=""`, which `plan()` matches against no item at
#: all, so `ddflow_next` returned an empty queue on every call. The CLI kept working because
#: argparse supplied "task" and the MCP path no longer went through argparse. A default
#: that lives only in the parser is a default the typed layer silently drops.
DEFAULT_NEXT_KIND = "task"

#: How long `wait` blocks when the caller does not say. Long enough to outlast most
#: holders' remaining work, short enough that a forgotten wait does not hold a process
#: for an afternoon. A caller that wants longer asks again, which also re-checks that
#: waiting is still the right move.
DEFAULT_WAIT_TIMEOUT_S = 600

#: Where `ddflow render` writes the human-readable views when nobody says otherwise.
DEFAULT_RENDER_DIR = "docs/ddflow"
