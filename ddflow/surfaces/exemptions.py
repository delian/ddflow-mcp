"""What each command deliberately does NOT do on the other surface, as declared fields.

`tests/test_mcp_parity.py` used to hold these as six dicts (`NOT_EXPOSED`, `ALIASES`,
`LEAF_NOT_EXPOSED`, `LEAF_VIA`, `FLAG_EXEMPTIONS`, `PROSE_TOOLS`) beside the code they
excuse. They are fields of the command registry now (D-unify, B-uni-cmd-core): a `Command`
carries ``reason`` (why it has no MCP tool of its own), ``via`` (the tool, and the argument
value, that serves it), ``flag_exempt`` (CLI flags its tool omits) and ``prose_reason`` (why
its body is text). The parity test DERIVES its tables from `DECLARATIONS` -- these, and the
commands that moved onto the registry (`surfaces/declared/`, `commands/viewers_*`) with their
own -- so an exemption is declared once, beside a Command.

Each entry carries its reason; the ratchet is that the list may only shrink.
"""

from __future__ import annotations

from .commands import viewers_lists as _lists
from .commands import viewers_search as _search
from .commands import viewers_sessions as _sessions
from .declared.flow import COMMANDS as _FLOW
from .declared.hooks import COMMANDS as _HOOKS
from .declared.knowledge import COMMANDS as _KNOWLEDGE
from .declared.lifecycle import COMMANDS as _LIFECYCLE
from .declared.memory import COMMANDS as _MEMORY
from .declared.queue import COMMANDS as _QUEUE
from .declared.records import COMMANDS as _RECORDS
from .declared.reporting import COMMANDS as _REPORTING
from .declared.review import COMMANDS as _REVIEW
from .declared.rules import COMMANDS as _RULES
from .declared.setup import COMMANDS as _SETUP
from .registry import (
    Command,
    covering_tools,
    declared_words,
    exempt_paths,
    flag_exemptions,
    prose_reasons,
    routed_paths,
)

#: Declarations about commands whose two surfaces differ on purpose. A tool-only entry
#: (`path=()`) says something about an MCP tool whose CLI twin is declared elsewhere.
EXEMPTIONS: tuple[Command, ...] = (
    Command(
        path=("mcp",),
        reason="starts the MCP server itself; exposing it over MCP would be recursive",
    ),
    Command(
        path=(),
        tool="ddflow_ci",
        flag_exempt={
            "--stage": "`ci record` is for the pre-push hook script, which has a shell and no MCP session",
            "--result": "`ci record`: see --stage",
            "--report": "`ci record`: see --stage",
            "--sha": "`ci record`: see --stage",
        },
    ),
    Command(
        path=(),
        tool="ddflow_list",
        flag_exempt={
            "--kind": "carried by `sources`: `kind` selects the viewer",
            "--exact": "carried by `mode`=exact",
            "--regex": "carried by `mode`=regex",
        },
    ),
    Command(
        path=(),
        tool="ddflow_export",
        flag_exempt={
            "--update": "MCP writes with write=true plus a repo-relative path",
            "--out": "MCP: write=true plus path (the same path-safety rules)",
            "--force": "overriding hand-edit protection is the operator's, at a terminal",
            "--template": "an agent never feeds the renderer an arbitrary file",
            "--lock": "the operator's veto: a person at a terminal locks a document",
            "--local": "a per-machine selection is the operator's, at a terminal",
            "--yes": "answers the terminal confirmation, which MCP has none of",
        },
    ),
    Command(
        path=(),
        tool="ddflow_bisect",
        flag_exempt={
            "--glob": "where candidates come from stays the default tests/**/test_*.py over MCP; an agent names `candidates` when the suite lives elsewhere (tools/list byte budget)",
            "--repeat": "re-running each probe is a terminal-side choice for a flaky pollution (tools/list byte budget)",
            "--max-runs": "the run budget is the operator's, set at a terminal; the default 200 bounds an agent's call (tools/list byte budget)",
        },
    ),
    Command(
        path=(),
        tool="ddflow_brief",
        prose=True,
        prose_reason="a budgeted reading pack — rules, decisions and lessons as text to read",
    ),
)

#: Every declaration about the two surfaces' differences: these, and those a migrated command
#: carries itself (D-unify 4: a command moved onto the registry moves its exemption with it).
DECLARATIONS: tuple[Command, ...] = (
    *EXEMPTIONS,
    *_KNOWLEDGE,
    *_RECORDS,
    *_QUEUE,
    *_LIFECYCLE,
    *_RULES,
    *_REVIEW,
    *_SETUP,
    *_REPORTING,
    *_MEMORY,
    *_FLOW,
    *_HOOKS,
    *_lists.COMMANDS,
    *_sessions.COMMANDS,
    _search.COMMAND,
)

#: The tables `tests/test_mcp_parity.py` checks the surfaces against, derived once.
EXEMPT_PATHS = exempt_paths(DECLARATIONS)
ROUTED_PATHS = routed_paths(DECLARATIONS)
COVERING_TOOLS = covering_tools(DECLARATIONS)
EXEMPT_WORDS = declared_words(DECLARATIONS)
FLAG_EXEMPT = flag_exemptions(DECLARATIONS)
PROSE_REASONS = prose_reasons(DECLARATIONS)
