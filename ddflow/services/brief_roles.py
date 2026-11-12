"""Role packs: the project's rules for a role as one short text (`ddflow brief --role R`).

The lines come from `[roles]` config (`<role>_rules`, overridable per layer); a line
`rule:<id>` is rendered from that rule record's title. Nothing here holds prose of its own.
The pack is cut at a line boundary to `[roles].budget_tokens` and says so.
"""

from __future__ import annotations

from ..core import defs as D
from ..core.budget import approx_tokens

ROLES = ("phase-closer", "fixer", "researcher", "reviewer")
RULE_PREFIX = "rule:"


def unknown_role(role: str) -> str:
    """The refusal for a role that is not one of `ROLES`, naming them all."""
    return f"no role {role!r}: the roles are {', '.join(ROLES)}"


def _line(st, text: str) -> str:
    if not text.startswith(RULE_PREFIX):
        return text
    rid = text[len(RULE_PREFIX) :].strip()
    rec = st.defs.get(D.key("rule", rid))
    if rec is None or not rec.live:
        return f"[{rid}] (no such rule: `ddflow rule list`)"
    return f"[{rid}] {rec.fields.get('title', '')}".rstrip()


def render(cfg, st, role: str, *, item: str = "", phase: str = "") -> str:
    """The role pack text for ``role``, scoped to an item or phase when one is named."""
    packs = {
        "phase-closer": cfg.roles.phase_closer_rules,
        "fixer": cfg.roles.fixer_rules,
        "researcher": cfg.roles.researcher_rules,
        "reviewer": cfg.roles.reviewer_rules,
    }
    lines = packs[role]
    head = f"# Role: {role}" + (f" ({phase})" if phase else "") + (f" on {item}" if item else "")
    body = [f"- {_line(st, str(x))}" for x in lines]
    budget = int(cfg.roles.budget_tokens)
    notice = "(more rules cut to the budget; `[roles].budget_tokens`)"
    text = "\n".join([head, *body])
    if budget <= 0 or approx_tokens(text) <= budget:
        return text
    kept = [head]
    for line in body:
        if approx_tokens("\n".join([*kept, line, notice])) > budget:
            break
        kept.append(line)
    out = "\n".join([*kept, notice])
    if approx_tokens(out) > budget:  # not even one rule and the notice fit: the head alone
        out = head
    return out
