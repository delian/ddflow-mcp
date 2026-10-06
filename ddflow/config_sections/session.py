"""The `[session]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass, field

from ._docs import _doc


@dataclass
class SessionConfig:
    """Prompt/session logging for recovery and from-scratch reconstruction."""

    log_prompts: bool = True
    redact_patterns: list[str] = field(
        default_factory=lambda: [
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
        ]
    )
    redact_extra: list[str] = field(default_factory=list)
    brief_max_tokens: int = 1200
    brief_lesson_count: int = 4
    replay_verify_diffs: bool = True
    #: The progress block `complete` adds: on | phase | off (see services/progress_line).
    progress_after_complete: str = "on"
    #: Characters of transcript the PreCompact hook keeps as a session note (0 = off).
    compaction_digest_chars: int = 2000


_doc(
    "session",
    "compaction_digest_chars",
    "Before Claude Code compacts the context, the PreCompact hook (`ddflow hooks install --claude`) records the last turns of the transcript -- the operator's words and the agent's answers, no tool traffic, secrets redacted -- as a session note of at most this many characters, and the SessionStart hook hands it back after compaction. The hook gets no summary from Claude Code, so this digest is the record of what the session was doing. 0 turns it off.",
)
_doc(
    "session",
    "progress_after_complete",
    "After every completion, a few lines on where the work stands: tasks, bugs (fixed, open, high) and phases done of total with percentages, the item's phase, and the next ready items. on (default) | phase (only the phase line and next) | off. The agent relays it to the operator; turn it off with `ddflow config session.progress_after_complete off`, or ask the agent to.",
)
_doc(
    "session",
    "log_prompts",
    "Record every operator prompt verbatim as an event. This is what makes 'rebuild the project from the logs alone' possible; turning it off forfeits that.",
)
_doc(
    "session",
    "redact_patterns",
    "Regexes applied to every logged prompt and note before it touches disk. The log is committed, so an unredacted secret is a leaked secret.",
)
_doc(
    "session",
    "redact_extra",
    "More redaction regexes, applied IN ADDITION to redact_patterns. Setting redact_patterns replaces the built-in secret patterns, so a project that copies them to add one of its own freezes them; add yours here instead -- e.g. private-network addresses, so an operator prompt naming a LAN host is masked before it reaches the committed log.",
)
_doc(
    "session",
    "brief_max_tokens",
    "Ceiling on the session-start brief. The brief replaces reading the rulebooks and the lessons file; it must stay small or it defeats itself.",
)
_doc(
    "session",
    "brief_lesson_count",
    "How many task-relevant lessons the brief carries. Retrieved by relevance to the active task text, not by recency.",
)
_doc(
    "session",
    "replay_verify_diffs",
    "During `ddflow replay --verify`, check that each recorded commit still exists and its diff still applies. Catches a log that has drifted from the tree it claims to describe.",
)
