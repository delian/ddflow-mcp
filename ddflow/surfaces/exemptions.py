"""What each command deliberately does NOT do on the other surface, as declared fields.

`tests/test_mcp_parity.py` used to hold these as six dicts (`NOT_EXPOSED`, `ALIASES`,
`LEAF_NOT_EXPOSED`, `LEAF_VIA`, `FLAG_EXEMPTIONS`, `PROSE_TOOLS`) beside the code they
excuse. They are fields of the command registry now (D-unify, B-uni-cmd-core): a `Command`
carries ``reason`` (why it has no MCP tool of its own), ``via`` (the tool, and the argument
value, that serves it), ``flag_exempt`` (CLI flags its tool omits) and ``prose_reason`` (why
its body is text). The parity test DERIVES its tables from `EXEMPTIONS`, so an exemption is
declared once, beside a Command, and a migrated command moves its declaration onto itself.

Each entry carries its reason; the ratchet is that the list may only shrink.
"""

from __future__ import annotations

from .commands import viewers_lists as _lists
from .commands import viewers_search as _search
from .commands import viewers_sessions as _sessions
from .declared.answer import ANSWER_FLAG_EXEMPT
from .declared.knowledge import COMMANDS as _KNOWLEDGE
from .declared.records import COMMANDS as _RECORDS
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
        path=("approve",),
        reason=(
            "clears a HUMAN-approval gate, and the whole point is that the agent cannot. "
            "A human checkpoint reachable from the MCP surface is not a human checkpoint "
            "\u2014 it is a second `gate record` with a longer name. This exemption is the "
            "feature, not an oversight, and `test_no_mcp_tool_can_clear_a_human_gate` "
            "asserts it holds end to end rather than resting on this line."
        ),
    ),
    Command(
        path=("version", "lint"),
        reason="the release lint runs inside ddflow_version_cut (also with dry_run), and its waiver is the operator's decision, from the CLI; a tool of its own would cost every client's tools/list for a check only a release-maker runs",
    ),
    Command(
        path=("hooks", "status"), reason="covered by ddflow_hooks, whose action argument selects it"
    ),
    Command(
        path=("hooks", "install"),
        reason="covered by ddflow_hooks, whose action argument selects it",
    ),
    Command(
        path=("hooks", "uninstall"),
        reason="covered by ddflow_hooks, whose action argument selects it",
    ),
    Command(
        path=("prompts", "list"),
        reason="covered by ddflow_prompts, whose action argument selects it",
    ),
    Command(
        path=("prompts", "show"),
        reason="covered by ddflow_prompts, whose action argument selects it",
    ),
    Command(
        path=("prompts", "eject"),
        reason="covered by ddflow_prompts, whose action argument selects it",
    ),
    Command(
        path=("prompts", "get"),
        reason="covered by ddflow_prompts, whose action argument selects it",
    ),
    Command(path=("companions", "list"), reason="covered by ddflow_companions"),
    Command(path=("companions", "add"), reason="covered by ddflow_companions_add"),
    Command(
        path=("config",),
        reason="covered by ddflow_configure, which reads and writes the same knobs",
        via=("ddflow_configure",),
    ),
    Command(
        path=("hooks", "session-start"),
        reason="invoked BY the Claude Code SessionStart hook to put the brief into a new session; over MCP that is ddflow_brief",
    ),
    Command(
        path=("hooks", "pre-compact"),
        reason="invoked by Claude Code's own PreCompact hook with its JSON on stdin; an agent never calls it, and the record it writes is a session note (ddflow_session_note)",
    ),
    Command(
        path=("hooks", "prompt"),
        reason="invoked BY the harness's prompt hook with the prompt's JSON on stdin; an agent records its own words with ddflow_session_prompt",
    ),
    Command(
        path=("hooks", "check-msg"),
        reason="invoked BY the installed commit-msg hook with the message being committed; it is not something an agent calls",
    ),
    Command(
        path=("hooks", "check-commit"),
        reason="invoked BY the installed git hook, inside the commit that is being checked; it is not something an agent calls",
    ),
    Command(
        path=("reviewers", "presets"),
        reason="lists boilerplate for authoring reviewer config, which pairs with `reviewers add` — an operator edit, exempt for the same reason",
    ),
    Command(
        path=("reviewers", "add"),
        reason="writes an API-key env-var name into project config; a config edit an operator should make deliberately, not an agent mid-task",
    ),
    Command(
        path=("reviewers", "approve"),
        reason="a PERSON vouches for a tool-written reviewer (decision D-reviewer-trust); an agent that could approve the reviewer it wrote would make the record decorative. test_approve_is_not_an_mcp_tool asserts there is no such tool.",
    ),
    Command(path=("reviewers", "detect"), reason="covered by ddflow_reviewers_detect"),
    Command(path=("reviewers", "list"), reason="covered by ddflow_reviewers_list"),
    Command(
        path=("reviewers", "test"),
        reason="covered by ddflow_reviewers_detect, which probes the same way",
    ),
    Command(path=("adopt",), reason="covered by ddflow_setup", via=("ddflow_setup",)),
    Command(path=("init",), reason="covered by ddflow_setup", via=("ddflow_setup",)),
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
        path=(), tool="ddflow_verify", flag_exempt={"--all": "a sweep is what omitting `id` means"}
    ),
    Command(
        path=(),
        tool="ddflow_doctor",
        flag_exempt={"--upgrade": "the same as `ddflow upgrade`, which is `ddflow_upgrade`"},
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
        tool="ddflow_review",
        flag_exempt={
            "--finding": "belongs to `review triage`, which is ddflow_review_triage (plain `review` refuses them)",
            "--refuted": "belongs to `review triage`, which is ddflow_review_triage (plain `review` refuses them)",
            "--confirmed": "belongs to `review triage`, which is ddflow_review_triage (plain `review` refuses them)",
            "--probe": "belongs to `review triage`, which is ddflow_review_triage (plain `review` refuses them)",
            "--force": "lifting the review-round budget belongs to the operator",
            "--reason": "lifting the review-round budget belongs to the operator",
        },
    ),
    Command(
        path=(),
        tool="ddflow_gate_skip",
        flag_exempt={
            "--outcome": "a skip IS the outcome",
            "--evidence": "a skipped gate produced none; that is what skipped means",
            "--command": "nothing ran",
            "--exit-code": "nothing ran",
            "--output-file": "nothing ran",
            "--model": "no reviewer performed it",
        },
    ),
    Command(
        path=(),
        tool="ddflow_import",
        flag_exempt={"--verify": "covered by ddflow_import_verify, its own tool"},
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
        tool="ddflow_phase_add",
        flag_exempt=ANSWER_FLAG_EXEMPT,
    ),
    Command(
        path=(),
        tool="ddflow_task_add",
        flag_exempt=ANSWER_FLAG_EXEMPT,
    ),
    Command(
        path=(),
        tool="ddflow_memory_add",
        flag_exempt=ANSWER_FLAG_EXEMPT,
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
    Command(
        path=(),
        tool="ddflow_board",
        prose=True,
        prose_reason="a rendered markdown board, meant to be shown or committed as-is",
    ),
    Command(
        path=(),
        tool="ddflow_gate_status",
        prose=True,
        prose_reason="carries the next gate's INSTRUCTION, which is the useful half",
    ),
    Command(
        path=(),
        tool="ddflow_gate_list",
        prose=True,
        prose_reason="one row per gate (or per gate passed on refutation), meant to be read as lines",
    ),
    Command(
        path=(),
        tool="ddflow_replay",
        prose=True,
        prose_reason="the reconstruction narrative; the whole output is the deliverable",
    ),
    Command(
        path=(),
        tool="ddflow_doctor",
        prose=True,
        prose_reason="a health report written to be read, with remedies in prose",
    ),
    Command(
        path=(),
        tool="ddflow_configure",
        prose=True,
        prose_reason="prints every knob with its documentation and its source",
    ),
    Command(
        path=(),
        tool="ddflow_setup",
        prose=True,
        prose_reason="a checklist of what it wrote and what to do next",
    ),
    Command(
        path=(),
        tool="ddflow_review",
        prose=True,
        prose_reason="reviewer findings, already formatted with their severities",
    ),
    Command(
        path=(),
        tool="ddflow_review_triage",
        prose=True,
        prose_reason="one confirmation line, which is the whole answer",
    ),
    Command(
        path=(),
        tool="ddflow_reviewers_list",
        prose=True,
        prose_reason="a table, plus the warning about unclassified reviewers",
    ),
    Command(
        path=(),
        tool="ddflow_reviewers_detect",
        prose=True,
        prose_reason="a probe report naming each endpoint and what answered",
    ),
    Command(
        path=(),
        tool="ddflow_render",
        prose=True,
        prose_reason="with --show it returns the rendered view itself, to read or commit",
    ),
    Command(
        path=(),
        tool="ddflow_prompts",
        prose=True,
        prose_reason="with show it returns the template itself, which is the thing to read",
    ),
)

#: Every declaration about the two surfaces' differences: these, and those a migrated command
#: carries itself (D-unify 4: a command moved onto the registry moves its exemption with it).
DECLARATIONS: tuple[Command, ...] = (
    *EXEMPTIONS,
    *_KNOWLEDGE,
    *_RECORDS,
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
