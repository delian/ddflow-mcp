"""The `[session]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ._docs import declare, int_at_least, knob

#: What `[session].progress_after_complete` accepts (services/progress_line).
PROGRESS_MODES = ("on", "phase", "off")

#: The sections of the standard subagent launch brief (`ddflow brief --for-agent`), in the order
#: `templates/prompts/agent_brief.md` carries them; `agent_brief_off` names the ones to leave out.
AGENT_BRIEF_SECTIONS = (
    "identity",
    "item",
    "rules",
    "decisions",
    "lessons",
    "start",
    "tests",
    "reviews",
    "gates",
    "finish",
    "forbidden",
    "filing",
    "project",
    "report",
)


#: A key of `[session.context_budget]`: `global`, or an agent type named as `ddflow harness list` does.
_BUDGET_KEY = re.compile(r"[a-z][a-z0-9_-]*")


def context_budget_problem(value: object) -> str:
    """Why a ``[session.context_budget]`` table is unusable, or ``""``: keys are `global` or an
    agent type id (lower case), values whole numbers of tokens, 0 or more."""
    if not isinstance(value, dict):
        return "must be a table of token budgets, e.g. { global = 30000, claude = 20000 }"
    for key, tokens in value.items():
        if not isinstance(key, str) or not _BUDGET_KEY.fullmatch(key):
            return f"key {key!r} must be `global` or an agent type id such as claude or codex"
        if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 0:
            return f"{key}: must be a whole number of tokens, 0 or more (0 = no ceiling)"
    return ""


def _check_brief_sections(value) -> str:
    bad = [x for x in value if x not in AGENT_BRIEF_SECTIONS]
    return f"unknown section(s) {bad}; known: {', '.join(AGENT_BRIEF_SECTIONS)}" if bad else ""


@declare("session")
@dataclass
class SessionConfig:
    """Prompt/session logging for recovery and from-scratch reconstruction."""

    log_prompts: bool = knob(
        True,
        doc="Record every operator prompt verbatim as an event. This is what makes 'rebuild the project from the logs alone' possible; turning it off forfeits that.",
    )
    redact_patterns: list[str] = knob(
        factory=lambda: [
            # `key: value` and `key=value`.
            r"(?i)(api[-_ ]?key|token|secret|password|passwd|pwd)\s*[:=]\s*\S+",
            # `Authorization: Bearer <token>` — a SPACE, not a colon, after the scheme.
            # The colon-or-equals pattern above does not match it, so bearer tokens were
            # written to the committed log in full.
            r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._~+/=-]{12,}",
            r"(?i)\b(gh[pousr]_[A-Za-z0-9]{16,})\b",
            r"(?i)\b(sk-[A-Za-z0-9_-]{16,})\b",
            r"(?i)\b(xox[abprs]-[A-Za-z0-9-]{10,})\b",
            r"\bAKIA[0-9A-Z]{16}\b",
            # The WHOLE PEM block, not just its header. Matching the header alone left
            # the base64 key body — the actual secret — in the log.
            r"(?s)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            r"(?s)-----BEGIN OPENSSH PRIVATE KEY-----.*?-----END OPENSSH PRIVATE KEY-----",
        ],
        doc="Regexes applied to every logged prompt and note before it touches disk. The log is committed, so an unredacted secret is a leaked secret.",
    )
    redact_extra: list[str] = knob(
        factory=list,
        doc="More redaction regexes, applied IN ADDITION to redact_patterns. Setting redact_patterns replaces the built-in secret patterns, so a project that copies them to add one of its own freezes them; add yours here instead -- e.g. private-network addresses, so an operator prompt naming a LAN host is masked before it reaches the committed log.",
    )
    redact_lookup_network: bool = knob(
        True,
        doc="Mark private-network addresses and private host names (.lan, .local, .internal, .home.arpa) even in the event fields that are looked up rather than read (urls, paths, branches, tags, globs), which are otherwise written as given. The log is committed and pushed, so a LAN address in a pull-request url is a leak; turn this off only if such a field must keep an address byte for byte.",
    )
    brief_max_tokens: int = knob(
        1200,
        doc="Ceiling on the session-start brief. The brief replaces reading the rulebooks and the lessons file; it must stay small or it defeats itself.",
    )
    rules_charter_tokens: int = knob(
        300,
        doc="Tokens, inside brief_max_tokens, reserved FIRST for the rules charter: one line (id and title) for every live project rule, never cut by the sections after it. It also heads the subagent launch brief. 0 turns the charter off; it never takes more than half of brief_max_tokens. What does not fit is counted and points to `ddflow rule list`.",
    )
    rules_charter_title_chars: int = knob(
        100,
        doc="Characters of a rule's title the rules charter line keeps.",
    )
    claim_rules_reserved: int = knob(
        2,
        doc="How many rules governing the work every guidance block (the claim, the gate and review prompts) quotes before the decisions spend the budget: rules are advisory and would otherwise rank below every warning. 0 ranks them like any other guidance.",
    )
    brief_lesson_count: int = knob(
        4,
        doc="How many task-relevant lessons the brief carries. Retrieved by relevance to the active task text, not by recency.",
    )
    context_budget_tokens: int = knob(
        40000,
        doc="Ceiling, in tokens, on what ddflow itself costs a session before its first turn: the MCP instructions, `tools/list` for the active `[mcp].tools` profile and the session brief (what the SessionStart hook prints) plus `start_notice_reserve_tokens` for the one-line upgrade notice, which cannot be counted without using it up. `ddflow doctor` prints `session start cost: N tokens (budget M)` and says OVER when it is past; tests/test_session_start_budget.py ratchets the same measurement. 0 sets no budget (the cost is still shown).",
        check=int_at_least(0),
    )
    context_budget: dict[str, int] = knob(
        factory=dict,
        doc="Start budgets per agent type, beside the global `context_budget_tokens`: a table from `global` or an agent type (the harness ids of `ddflow harness list`: claude, codex, copilot, cursor, dsh, ...) to a ceiling in tokens, e.g. { claude = 20000, codex = 30000 }, so each subscription or LLM target is managed separately. A session's effective budget is the smaller of the global and its type's; `global` here is another spelling of `context_budget_tokens`: the same value, the later layer's spelling winning; a type left out inherits the global; 0 sets no ceiling AT THAT LEVEL (a type at 0 leaves the global in force). `ddflow doctor` reports the cost against each configured type. Layers merge key by key (the local layer wins).",
        check=context_budget_problem,
    )
    start_notice_reserve_tokens: int = knob(
        100,
        doc="Tokens `ddflow doctor` adds to the session start cost for the one-line upgrade or release notice that leads the first session after an upgrade. Measuring cannot count a pending notice without using it up (it is said once per version), so this reserve stands for it: the budget then holds even in that session. 0 reserves nothing.",
        check=int_at_least(0),
    )
    start_bytes_per_token: float = knob(
        4.0,
        doc="Bytes (UTF-8) to a token in the session start cost estimate (`context_budget_tokens`). Four suits prose; JSON such as `tools/list` runs nearer three, so lower it to be stricter. No tokenizer is imported.",
        check=lambda v: (
            ""
            if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v < float("inf")
            else "must be a finite number above 0"
        ),
    )
    first_call: bool = knob(
        True,
        doc="Append a compact session start to the first MCP tool call of a connection, for agents whose hooks and instructions do not deliver one. Off when a native hook already delivers it for the agent's harness.",
    )
    first_call_max_tokens: int = knob(
        600,
        doc="Ceiling on the session-start text the first MCP call carries. Small by default: it rides on every new connection.",
    )
    replay_verify_diffs: bool = knob(
        True,
        doc="During `ddflow replay --verify`, check that each recorded commit still exists and its diff still applies. Catches a log that has drifted from the tree it claims to describe.",
    )
    #: The progress block `complete` adds: on | phase | off (see services/progress_line).
    progress_after_complete: str = knob(
        "on",
        doc="After every completion, a few lines on where the work stands: tasks, bugs (fixed, open, high) and phases done of total with percentages, the item's phase, and the next ready items. on (default) | phase (only the phase line and next) | off. The agent relays it to the operator; turn it off with `ddflow config session.progress_after_complete off`, or ask the agent to.",
        choices=PROGRESS_MODES,
        strictest=("on", "no safety dimension; reports the most"),
    )
    #: Characters of transcript the PreCompact hook keeps as a session note (0 = off).
    compaction_digest_chars: int = knob(
        2000,
        doc="Before Claude Code compacts the context, the PreCompact hook (`ddflow hooks install --claude`) records the last turns of the transcript -- the operator's words and the agent's answers, no tool traffic, secrets redacted -- as a session note of at most this many characters, and the SessionStart hook hands it back after compaction. The hook gets no summary from Claude Code, so this digest is the record of what the session was doing. 0 turns it off.",
    )
    agent_brief_max_chars: int = knob(
        9000,
        doc="Ceiling, in characters, on the launch brief `ddflow brief --for-agent NAME ITEM` renders for a subagent. Over it, the lessons and then the decisions are cut (down to a pointer); the standard sections are only ever left out by `agent_brief_off`. 0 means no ceiling.",
    )
    agent_brief_off: list[str] = knob(
        factory=list,
        doc="Sections of the launch brief to leave out: identity, item, rules, decisions, lessons, start, tests, reviews, gates, finish, forbidden, filing, project, report. Turning one off removes that standing rule from every launch brief: only for a project whose own instruction file already says it.",
        check=_check_brief_sections,
    )
    agent_brief_decisions: int = knob(
        6,
        doc="How many of the decisions that govern the item's files the launch brief quotes (the pinned ones first, then those whose own globs overlap the item's, operators' before agents'); `ddflow decision applicable ITEM` lists all.",
    )
    agent_brief_pin_decisions: list[str] = knob(
        factory=list,
        doc="Decision ids every launch brief (`ddflow brief --for-agent`) quotes first, whatever files the item declares: the project's standing decisions. They are never cut by the decision count; the character ceiling cuts them last. Unknown ids are ignored.",
    )
    report_commits: int = knob(
        20,
        doc="How many commit shas `ddflow report ITEM` lists for the landing before it says how many more there were.",
    )
    report_note_chars: int = knob(
        200,
        doc="Characters `ddflow report ITEM` keeps of a gate's reason, of the evidence text of a gate recorded by hand and of an earlier-tree note.",
    )
    report_title_chars: int = knob(
        100,
        doc="Characters `ddflow report ITEM` keeps of the title of each bug, task, lesson, research note and decision it lists as filed. Change it for the project (`ddflow config --set session.report_title_chars 140`), for this machine (add --local), or over MCP with `ddflow_configure`.",
    )
    report_summary_chars: int = knob(
        120,
        doc="Characters `ddflow report ITEM` keeps of the summary of each bug the item fixed, in its regression-test rows. Change it for the project (`ddflow config --set session.report_summary_chars 200`), for this machine (add --local), or over MCP with `ddflow_configure`.",
    )
    report_brief_chars: int = knob(
        200,
        doc="Characters `ddflow report ITEM --brief` keeps of each free-text entry (an override reason, a not-done note); ids, gate names, test names and the landing sha are never cut. Change it for the project (`ddflow config --set session.report_brief_chars 300`), for this machine (add --local), or over MCP with `ddflow_configure`.",
    )
    report_test_files: int = knob(
        12,
        doc="How many changed test files `ddflow report ITEM` names before it says how many more there were.",
    )
    agent_brief_lessons: int = knob(
        3,
        doc="How many task-relevant lessons the launch brief carries.",
    )
    agent_brief_extra: str = knob(
        "",
        doc="Project text added to every launch brief as its `project` section: the commands and rules that hold only here (how to run the full test suite, a forbidden path). The standard text stays in one place; this is the project's addition to it.",
    )
