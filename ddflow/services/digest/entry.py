"""One operator-queue entry, the facts a collector may be handed, and the shared helpers
(OD-queue, D-OD-Q2/Q3).

Every function here is pure: the same State, Config, `now` and `Facts` give the same entries.
The text of an entry comes from templates and recorded fields, never from agent prose when a
field exists; an agent's own words (an ask's reason) are not copied into `what` or `why`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ...config_sections.digest import LADDER_STEPS
from ...core import clock
from ...core.health import KINDS as HK
from ...core.model import State
from ..redact_report import redact_secrets
from .sources import SEVERITIES, by_kind

#: Facts that need I/O (a worktree's newest change) are read by the caller and passed in; a
#: queue built without them says so (`Queue.gaps`) instead of reporting a clean lane.
HEALTH_KINDS = HK  # every finding kind the watchdog can raise


@dataclass(frozen=True)
class Facts:
    """What a pure collector cannot read from State. ``health`` is None when nobody checked."""

    health: tuple[Any, ...] | None = None


@dataclass(frozen=True)
class Entry:
    id: str
    kind: str
    owner: str
    what: str
    why: str
    #: When it first became true (the log's own timestamp); the age counts from here.
    since: str
    #: Epoch seconds of `since`; 0 when the log gave no parsable time.
    since_s: float
    default: str
    answer: str
    command: str
    chat: str
    #: Item ids that cannot go on until it is answered, and the agents holding them.
    blocks: tuple[str, ...] = ()
    agents: tuple[str, ...] = ()
    #: Open items transitively behind `blocks` (the count shown as "blocks N").
    impact: int = 0
    severity: str = "nudge"
    age_s: float = 0.0
    #: Free fields a renderer shows beside the entry (counts, verbs), already plain text.
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "owner": self.owner,
            "what": self.what,
            "why": self.why,
            "since": self.since,
            "age_s": round(self.age_s),
            "default": self.default,
            "answer": self.answer,
            "command": self.command,
            "chat": self.chat,
            "blocks": list(self.blocks),
            "agents": list(self.agents),
            "impact": self.impact,
            "severity": self.severity,
            "detail": self.detail,
        }


def parse_ladder(text: str) -> tuple[float, float]:
    """``"1h, 24h"`` as the two ages (seconds) after which an entry is `needs` and `urgent`."""
    parts = [p.strip() for p in str(text).split(",") if p.strip()]
    if len(parts) != LADDER_STEPS:
        raise ValueError(f"{text!r}: give two ages, e.g. '1h, 24h'")
    first, second = (clock.parse_duration(p) for p in parts)
    if not 0 <= first <= second:
        raise ValueError(f"{text!r}: the first age must not exceed the second")
    return first, second


def rank(severity: str) -> int:
    return SEVERITIES.index(severity)


def severity_at(kind: str, age_s: float, cfg) -> str:
    """The kind's starting severity, lifted by age: `needs` after the first ladder age and
    `urgent` after the second (red main: `urgent` after `escalate_after_red_main`)."""
    start = by_kind()[kind].severity
    if kind == "red_main":
        lifted = (
            "urgent"
            if age_s >= clock.parse_duration(cfg.digest.escalate_after_red_main)
            else "needs"
        )
    else:
        first, second = parse_ladder(cfg.digest.escalate_after)
        floor = (
            start if rank(start) < rank("nudge") else "nudge"
        )  # an `info` kind starts below nudge
        lifted = "urgent" if age_s >= second else "needs" if age_s >= first else floor
    return start if rank(start) >= rank(lifted) else lifted


def dependants(st: State, roots: Iterable[str]) -> set[str]:
    """Open items that transitively `need` any of ``roots`` (the roots themselves excluded)."""
    wanted = set(roots)
    found: set[str] = set()
    grew = True
    while grew:
        grew = False
        for it in st.items.values():
            if it.id in found or it.id in wanted or it.removed or it.terminal:
                continue
            if any(n in wanted or n in found for n in it.needs):
                found.add(it.id)
                grew = True
    return found


def clip(text: str, cfg) -> str:
    """A recorded text for display: secrets masked, on one line, cut at `[digest].text_chars`."""
    clean, _ = redact_secrets(str(text or ""), cfg)
    return " ".join(clean.split())[: cfg.digest.text_chars]


def seconds(ts: str, default: float = 0.0) -> float:
    return clock.epoch(ts, default=default)


def make(  # noqa: PLR0913 -- an entry has this many facets; callers name each
    st: State,
    cfg,
    now: float,
    kind: str,
    key: str,
    *,
    what: str,
    why: str,
    since: str,
    default: str,
    command: str = "",
    answer: str = "",
    blocks: Iterable[str] = (),
    agents: Iterable[str] = (),
    detail: dict[str, Any] | None = None,
    chat_ref: str = "",
) -> Entry:
    """Build an entry from the source row of ``kind``: owner, chat route, answer text and the
    starting severity come from the table; age, impact and the ladder from State and Config."""
    row = by_kind()[kind]
    blocks = (
        (blocks,) if isinstance(blocks, str) else blocks
    )  # a bare id is one id, not its letters
    agents = (agents,) if isinstance(agents, str) else agents
    since_s = seconds(since)
    age = max(0.0, now - since_s) if since_s else 0.0
    blocked = tuple(sorted(dict.fromkeys(blocks)))
    subject = next(iter(blocked), key)  # what a template's <id>, <task> or <item> stands for
    cmd = command or row.command
    for ph in ("<id>", "<task>", "<item>", "<phase>"):
        cmd = cmd.replace(ph, subject)
    chat = row.chat.replace("{id}", chat_ref or f"{kind}:{key}")
    severity = severity_at(kind, age, cfg)
    if not since_s and rank(severity) < rank("needs"):
        severity = "needs"  # an unreadable time is never shown as 'just now': it is unknown, so it is visible
    return Entry(
        id=f"{kind}:{key}",
        kind=kind,
        owner=row.owner,
        what=what,
        why=why,
        since=since,
        since_s=since_s,
        default=default,
        answer=answer or row.answer,
        command=cmd,
        chat=chat,
        blocks=blocked,
        agents=tuple(sorted(dict.fromkeys(agents))),
        impact=len(dependants(st, blocked)) + len(blocked),
        severity=severity,
        age_s=age,
        detail=detail or {},
    )
