"""The `[dedupe]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass, field

from ._docs import _doc

#: The record kinds an add is checked for, and that are offered as candidates. Session
#: prompts and notes are not records anyone duplicates (D-no-duplicates). A rule is a
#: file, not a log record: it is CHECKED against every other kind when added or edited
#: (D-rule-dedupe-everywhere), and compared with the other rules by `api.rules`' own
#: check, but it is never itself offered as a candidate (the index holds no rules).
DEDUPE_KINDS = ("bug", "task", "phase", "lesson", "decision", "research", "memory", "rule")
DEDUPE_ON_MATCH = ("ask", "warn", "off")


@dataclass
class DedupeConfig:
    """Duplicate detection at add time, `[dedupe]` (decision D-no-duplicates)."""

    # "ask": every surface can answer one now -- --new / --extends / --duplicate-of /
    # --related on the CLI (a prompt on a terminal), `relation` over MCP. It was shipped
    # as "warn" while none could (hotfix B-dedupe-default-warn).
    on_match: str = "ask"  # ask | warn | off
    show_floor: float = 0.35
    ask_threshold: float = 0.55
    max_candidates: int = 3
    min_words: int = 8
    kinds: list[str] = field(default_factory=lambda: list(DEDUPE_KINDS))


_doc(
    "dedupe",
    "on_match",
    "What an add does when it looks like an existing record. 'ask' (default): it is refused until answered new / extends X / duplicate of X / related X -- --new / --extends ID / --duplicate-of ID / --related ID on the CLI (a prompt on a terminal, exit 3 with the ready commands for a script), `relation` over MCP. 'warn': the candidates are printed and the add goes ahead. 'off': no check. No score can tell a duplicate from a different bug in the same function (research R-dedupe-matchers), which is why the default asks rather than decides.",
)
_doc(
    "dedupe",
    "show_floor",
    "Cosine similarity (0-1) at which an existing record is listed beside an add, without asking. 0.35 from the research: below it, related records are rare and listing them is noise.",
)
_doc(
    "dedupe",
    "ask_threshold",
    "Cosine similarity (0-1) at which an add must be answered before it proceeds (under on_match = 'ask'). 0.55 from the research: on ddflow's labelled records about half of real duplicates score above it and over three quarters of what does is a duplicate or related record. Identical text, or text naming an existing id, asks whatever the score.",
)
_doc(
    "dedupe",
    "max_candidates",
    "Most similar records shown for one add (records whose id the new text names are shown as well). The research found the existing record in the top 3 for 95% of real duplicates.",
)
_doc(
    "dedupe",
    "min_words",
    "Distinct content words a record needs before a score alone makes an add ask. A two-word title shares most of its words with something; asking on it is noise. Shorter records still list candidates.",
)
_doc(
    "dedupe",
    "kinds",
    "Record kinds checked on add, and offered as candidates -- across kinds, so a new bug is shown the open task that fixes it. Default: bug, task, phase, lesson, decision, research, memory, rule. A rule (a file, not a log record) is checked on `rule add` and on a `rule edit` of its title or content against every other kind, and compared with the other rules by its own check; it is never offered as a candidate.",
)
