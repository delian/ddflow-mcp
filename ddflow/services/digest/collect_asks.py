"""Collectors for entries that are an open ask (OD-queue): a pending approval request or
question. The kind is chosen by the ask's verb through the source table, so a verb with its
own row (`gate.skip.failed_review` -> `gate_skip`) is not also reported as a generic approval.
"""

from __future__ import annotations

from shlex import quote
from typing import Any

from ..ask import engine as E
from . import entry as N
from .sources import SOURCES

SAFE_DEFAULT = "deny (safe: nothing changes and the agent can ask again)"

#: ask verb -> source kind, from the table; any other verb is a plain 'approval'.
_KIND_OF_VERB = {v: s.kind for s in SOURCES for v in s.sites}


def kind_of(row: dict[str, Any]) -> str:
    if not row["verb"] or row.get("type") == "question":
        return "question"
    return _KIND_OF_VERB.get(row["verb"], "approval")


def _item_of(subject: str) -> str:
    """The item of a `cmd:<verb>:<item>:<target>` subject (colons and percents escaped)."""
    parts = subject.split(":")
    if len(parts) < 3 or parts[0] != "cmd":  # noqa: PLR2004 -- cmd, verb, item
        return ""
    return parts[2].replace("%3A", ":").replace("%25", "%")


def _question_default(row: dict[str, Any]) -> str:
    opts = [str(o) for o in row.get("options") or []]
    return f"one of: {', '.join(opts)}" if opts else "answer in your own words"


def _entries(st, cfg, now, kind: str) -> list[N.Entry]:
    out = []
    for row in E.pending(st, now):
        if kind_of(row) != kind:
            continue
        item = _item_of(row["subject"])
        verb = row["verb"] if kind != "question" else ""
        who = row["requested_by"]
        out.append(
            N.make(
                st,
                cfg,
                now,
                kind,
                row["id"],
                what=(
                    f"{who} asks for `{verb}`" + (f" on {item}" if item else "")
                    if verb
                    else f"{who} asks (quoted, unchecked): {N.clip(row.get('text', ''), cfg)}"
                ),
                why=(
                    f"`{verb}` is a person-only act (tier {row['tier'] or '?'}); an agent cannot do it"
                    if verb
                    else "an agent asked and no one has answered"
                ),
                since=row["requested_at"],
                default=SAFE_DEFAULT if verb else _question_default(row),
                command=f"ddflow approvals {'approve' if verb else 'show'} {quote(row['id'])}",
                answer=f"approve or deny request {quote(row['id'])}" if verb else "",
                blocks=[item] if item in st.items else [],
                agents=[who],
                chat_ref=row["id"] if verb else "",
                detail={
                    "ask": row["id"],
                    "verb": verb,
                    **({} if verb else {"question": N.clip(row.get("text", ""), cfg)}),
                },
            )
        )
    return out


def collect_approval(st, cfg, now, facts):
    return _entries(st, cfg, now, "approval")


def collect_gate_skip(st, cfg, now, facts):
    return _entries(st, cfg, now, "gate_skip")


def collect_gate_partial(st, cfg, now, facts):
    return _entries(st, cfg, now, "gate_partial")


def collect_force_completion(st, cfg, now, facts):
    return _entries(st, cfg, now, "force_completion")


def collect_chore(st, cfg, now, facts):
    return _entries(st, cfg, now, "chore")


def collect_reviewer(st, cfg, now, facts):
    return _entries(st, cfg, now, "reviewer")


def collect_export_ack(st, cfg, now, facts):
    return _entries(st, cfg, now, "export_ack")


def collect_plan(st, cfg, now, facts):
    return _entries(st, cfg, now, "plan")


def collect_question(st, cfg, now, facts):
    return _entries(st, cfg, now, "question")
