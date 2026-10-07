"""The `[dedupe]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._docs import declare, knob

#: The record kinds an add is checked for, and that are offered as candidates. Session
#: prompts and notes are not records anyone duplicates (D-no-duplicates). A rule is a
#: file, not a log record: it is CHECKED against every other kind when added or edited
#: (D-rule-dedupe-everywhere), and compared with the other rules by `api.rules`' own
#: check, but it is never itself offered as a candidate (the index holds no rules).
DEDUPE_KINDS = ("bug", "task", "phase", "lesson", "decision", "research", "memory", "rule")
DEDUPE_ON_MATCH = ("ask", "warn", "off")


def _unit_interval(v: Any) -> str:
    """A similarity score bound: a number in [0, 1]. An int is fine (TOML `1`)."""
    ok = isinstance(v, int | float) and not isinstance(v, bool) and 0.0 <= v <= 1.0
    return "" if ok else "must be a number between 0 and 1"


@declare("dedupe")
@dataclass
class DedupeConfig:
    """Duplicate detection at add time, `[dedupe]` (decision D-no-duplicates)."""

    # "ask": every surface can answer one now -- --new / --extends / --duplicate-of /
    # --related on the CLI (a prompt on a terminal), `relation` over MCP. It was shipped
    # as "warn" while none could (hotfix B-dedupe-default-warn).
    #: ask | warn | off
    on_match: str = knob(
        "ask",
        doc="What an add does when it looks like an existing record. 'ask' (default): it is refused until answered new / extends X / duplicate of X / related X -- --new / --extends ID / --duplicate-of ID / --related ID on the CLI (a prompt on a terminal, exit 3 with the ready commands for a script), `relation` over MCP. 'warn': the candidates are printed and the add goes ahead. 'off': no check. No score can tell a duplicate from a different bug in the same function (research R-dedupe-matchers), which is why the default asks rather than decides.",
        choices=DEDUPE_ON_MATCH,
        strictest=("ask", "a likely duplicate is refused until answered"),
    )
    show_floor: float = knob(
        0.35,
        doc="Cosine similarity (0-1) at which an existing record is listed beside an add, without asking. 0.35 from the research: below it, related records are rare and listing them is noise.",
        check=_unit_interval,
    )
    ask_threshold: float = knob(
        0.55,
        doc="Cosine similarity (0-1) at which an add must be answered before it proceeds (under on_match = 'ask'). 0.55 from the research: on ddflow's labelled records about half of real duplicates score above it and over three quarters of what does is a duplicate or related record. Identical text, or text naming an existing id, asks whatever the score.",
        check=_unit_interval,
    )
    max_candidates: int = knob(
        3,
        doc="Most similar records shown for one add (records whose id the new text names are shown as well). The research found the existing record in the top 3 for 95% of real duplicates.",
        check=lambda v: (
            ""
            if isinstance(v, int) and not isinstance(v, bool) and v >= 1
            else 'must be an integer >= 1; to stop the check set [dedupe].on_match = "off"'
        ),
    )
    min_words: int = knob(
        8,
        doc="Distinct content words a record needs before a score alone makes an add ask. A two-word title shares most of its words with something; asking on it is noise. Shorter records still list candidates.",
        check=lambda v: (
            ""
            if isinstance(v, int) and not isinstance(v, bool) and v >= 0
            else "must be an integer >= 0"
        ),
    )
    kinds: list[str] = knob(
        factory=lambda: list(DEDUPE_KINDS),
        doc="Record kinds checked on add, and offered as candidates -- across kinds, so a new bug is shown the open task that fixes it. Default: bug, task, phase, lesson, decision, research, memory, rule. A rule (a file, not a log record) is checked on `rule add` and on a `rule edit` of its title or content against every other kind, and compared with the other rules by its own check; it is never offered as a candidate.",
        check=lambda v: (
            ""
            if isinstance(v, list) and v and all(k in DEDUPE_KINDS for k in v)
            else f"must be a non-empty list drawn from {', '.join(DEDUPE_KINDS)}; "
            'to stop the check set [dedupe].on_match = "off"'
        ),
    )
