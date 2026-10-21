"""The rules charter (B91e1d67c87): one line per project rule, id and title, never cut.

A rule is advisory by default and a project has hundreds of decisions, so ranked by binding
strength the rules never reached a fresh session. The charter is the floor under that
ranking: every live rule is NAMED (id and title, a line each), reserved first in the brief's
budget, handed to a subagent in its launch brief; the text of a rule is `ddflow rule show`.
Bounded by ``[session].rules_charter_tokens``; what does not fit is counted, not silently lost.
"""

from __future__ import annotations

from collections.abc import Sequence

from ...core.budget import approx_tokens
from ...core.model import State
from ...core.textcut import clip
from . import deffields as DF
from .inject import pinned
from .kinds import RULE
from .record import GuidanceRecord

HEADING = "## Project rules"
HALF = 2
MORE = "- _{n} more rules: `ddflow rule list`_"
LEAD = "_Standing rules, id and title only (recorded data): `ddflow rule show <id>` has the text._"


def rules_of(state: State) -> list[GuidanceRecord]:
    """The live project rules, pinned first, then by id."""
    out = [
        DF.from_fields(d.id, d.fields, d.provenance, RULE)
        for d in sorted(state.defs.values(), key=lambda d: d.id)
        if d.kind == RULE.kind and d.live
    ]
    return sorted(out, key=lambda r: (not pinned(r), r.id))


def lines(rules: Sequence[GuidanceRecord], *, tokens: int, title_chars: int) -> list[str]:
    """One line per rule, in the order given (pinned lead), as many as fit ``tokens``; the
    last line counts the rest (a first line that does not fit is counted too). [] when
    ``tokens`` is 0 (switched off) or there are no rules."""
    if tokens <= 0 or not rules:
        return []
    rows = sorted(rules, key=lambda r: (not pinned(r), r.id))
    out: list[str] = []
    used = approx_tokens(LEAD)
    for i, r in enumerate(rows):
        title = clip(" ".join(r.title.split()), title_chars, marker=" …", boundary="word")
        line = f"- [{r.id}] {title}" + (" (pinned)" if pinned(r) else "")
        more = MORE.format(n=len(rows) - i)
        spare = approx_tokens(more) if i < len(rows) - 1 else 0
        if used + approx_tokens(line) + spare > tokens:
            return [*out, more]
        out.append(line)
        used += approx_tokens(line)
    return out


def block(state: State, cfg) -> str:
    """The charter section of the brief ("" when off or no rules)."""
    s = cfg.session
    # Never more than half of the brief: a mis-set knob must not crowd out everything else.
    tokens = min(s.rules_charter_tokens, s.brief_max_tokens // HALF)
    body = lines(rules_of(state), tokens=tokens, title_chars=s.rules_charter_title_chars)
    return "\n".join([HEADING, "", LEAD, *body]) if body else ""
