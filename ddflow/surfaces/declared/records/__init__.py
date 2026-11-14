"""The record-keeping commands, declared once: research, bugs and sessions.

`surfaces/parsers/records.py` registers their command-line half and `surfaces/tools/` takes
their MCP entries (D-unify 4, B-uni-cmd-migrate.4-adapters). `bug reopen` has no tool of its
own: `ddflow_bug_invalid` with `reopen` serves it.

This package is the former ``declared/records.py`` (r-small-files), split into ``_shared.py``
(imports, constants, helpers) and ``research_bugs``, ``bugs_close`` and ``sessions`` (one
``Command`` area each). ``COMMANDS`` keeps every command in its exact original order and
``BY_TOOL`` is rebuilt from it; the names the single module held are re-exported lazily.
"""

from __future__ import annotations

from ddflow.core import _reexport

from ...registry import by_tool
from .bugs_close import COMMANDS as _BUGS_CLOSE
from .research_bugs import COMMANDS as _RESEARCH_BUGS
from .sessions import COMMANDS as _SESSIONS

COMMANDS: tuple = (*_RESEARCH_BUGS, *_BUGS_CLOSE, *_SESSIONS)
BY_TOOL = by_tool(COMMANDS)

#: Every module-level name the single file held that now lives in ``_shared``.
_SUBMODULE_OF = {
    "importlib": "_shared",
    "O": "_shared",
    "OK": "_shared",
    "CliPolicy": "_shared",
    "Command": "_shared",
    "Param": "_shared",
    "by_tool": "_shared",
    "_answer": "_shared",
    "_api": "_shared",
    "_bug_reopen": "_shared",
    "_bug_tag": "_shared",
    "_regression_tests": "_shared",
    "_reopening": "_shared",
    "_tagging": "_shared",
    "ANSWER_FLAG_EXEMPT": "_shared",
    "ANSWER_PARAMS": "_shared",
    "MUTATION_FLAG_EXEMPT": "_shared",
    "MUTATION_ORDER": "_shared",
    "MUTATION_PARAMS": "_shared",
    "mutation_of": "_shared",
    "BUG_FIXED_FIELDS": "_shared",
    "BUG_INVALID_FIELDS": "_shared",
    "file_tasks_call": "_shared",
    "_bug_dedupe_render": "_shared",
    "_bug_status_render": "_shared",
}

_reexport.reexport(globals(), _SUBMODULE_OF, __name__)
del _reexport

__all__ = ["BY_TOOL", "COMMANDS"]
