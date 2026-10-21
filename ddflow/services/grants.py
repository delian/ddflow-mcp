"""Operator grants (DX-operator-grants-recorded): the operator's permission for something
a budget refuses, recorded as an event a check reads instead of a message an agent relays.

A grant is an `approval.granted` (`services.approval`, the one approve-by-digest engine:
refused under an agent identity, the OS user and host stamped) whose subject is
``grant:<id>`` and whose ``terms`` say what it allows: an ``item`` and ``gate``, a ``kind``
(`KINDS`: today ``review_round``, one more review round past `[review].max_rounds`), a
``count``, a ``reason``, an optional ``when`` predicate and an ``expires`` time. Nothing
here is a second approval engine and no new event kind exists.

Use is DERIVED, not written: a review round that consumed a grant records its id as
``budget_grant`` in its evidence, and the grant's ``used`` is the number of the item's
recorded gate runs that cite it. A ``when`` makes the grant STANDING: `pick` applies it
without ``--force`` when its predicate holds on facts the review computed (`Ctx`), so the
operator's "extra round once the tree changed after triage" is checked, not trusted.

A new kind is `KINDS.register`; a new predicate is a key of its ``whens``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from typing import Any

from ..core import clock
from ..core.digest import content_digest
from ..core.model import State, fold
from ..core.registry import Registry
from . import approval as AP

PREFIX = "grant:"


class GrantRefused(ValueError):
    """A grant asked for under an agent identity: a person's act, refused (exit 3)."""


class GrantInvalid(ValueError):
    """A grant whose arguments are wrong (unknown kind, item or gate, a bad count, no
    reason); the message says which. A failure (exit 1), not a refusal."""


@dataclass(frozen=True)
class Ctx:
    """What a review knows when it asks whether a standing grant applies."""

    #: Findings of the gate's recorded round with no verdict.
    untriaged: int = 0
    #: The head the gate's last review covered ("" when none), and the head now.
    reviewed_head: str = ""
    head: str = ""


def tree_changed_after_triage(ctx: Ctx) -> bool:
    """Every finding of the last round has a verdict and the tree moved since that round."""
    return (
        bool(ctx.reviewed_head and ctx.head)
        and ctx.untriaged == 0
        and (ctx.head != ctx.reviewed_head)
    )


@dataclass(frozen=True)
class GrantKind:
    name: str
    doc: str
    #: predicate name -> test; a grant naming one is standing.
    whens: Mapping[str, Callable[[Ctx], bool]] = field(default_factory=dict)
    unit: str = "extra round"


#: The grant kinds, by name: a new kind is `KINDS.register(GrantKind(...))`.
KINDS: Registry[GrantKind] = Registry("grant kind", key=lambda k: k.name, order="registered")

KINDS.register(
    GrantKind(
        "review_round",
        "one more review round of a gate past [review].max_rounds",
        {"tree_changed_after_triage": tree_changed_after_triage},
    )
)


@dataclass(frozen=True)
class Grant:
    id: str
    item: str
    gate: str
    kind: str
    count: int
    reason: str
    when: str
    expires: str
    by: str
    at: str
    used: int = 0

    @property
    def standing(self) -> bool:
        return bool(self.when)

    @property
    def remaining(self) -> int:
        return max(0, self.count - self.used)

    def lapsed(self, now: float | None = None) -> bool:
        return bool(self.expires) and parse_expiry(self.expires) <= (
            time.time() if now is None else now
        )

    def usable(self, now: float | None = None) -> bool:
        return self.remaining > 0 and not self.lapsed(now)

    def row(self, now: float | None = None) -> dict[str, Any]:
        return {
            "id": self.id, "item": self.item, "gate": self.gate, "kind": self.kind,
            "count": self.count, "used": self.used, "remaining": self.remaining,
            "reason": self.reason, "when": self.when, "expires": self.expires,
            "expired": self.lapsed(now), "by": self.by, "at": self.at,
        }  # fmt: skip


def parse_expiry(text: str) -> float:
    return clock.parse_ts(text).timestamp()


def _used(st: State, item: str, gate: str, gid: str) -> int:
    it = st.items.get(item)
    return sum(1 for r in it.runs_of(gate) if r.evidence.get("budget_grant") == gid) if it else 0


def grants(st: State) -> list[Grant]:
    """Every recorded grant, oldest first. Only a grant a person made counts."""
    out = []
    for subject, rows in st.approvals.items():
        if not subject.startswith(PREFIX):
            continue
        for r in rows:
            t = r.get("terms") or {}
            if not r.get("human") or not t:
                continue
            gid = subject[len(PREFIX) :]
            out.append(
                Grant(
                    id=gid, item=t.get("item", ""), gate=t.get("gate", ""),
                    kind=t.get("kind", ""), count=int(t.get("count", 0)),
                    reason=t.get("reason", ""), when=t.get("when", ""),
                    expires=t.get("expires", ""), by=r.get("user", ""), at=r.get("at", ""),
                    used=_used(st, t.get("item", ""), t.get("gate", ""), gid),
                )
            )  # fmt: skip
    return sorted(out, key=_number)


def _number(g: Grant) -> int:
    """A grant's sequence number (G10 is newer than G2; the text order says otherwise)."""
    digits = g.id.lstrip("G")
    return int(digits) if digits.isdigit() else 0


def _next_id(st: State) -> str:
    return f"G{sum(1 for s in st.approvals if s.startswith(PREFIX)) + 1}"


def add(
    log,
    cfg,
    *,
    item: str,
    gate: str,
    kind: str,
    count: int,
    reason: str,
    when: str = "",
    requested_agent: str = "",
    gates: Collection[str],
) -> Grant:
    """A PERSON grants ``count`` of ``kind`` on ``item``.``gate``. Raises `GrantRefused`
    under an agent identity (first, before anything is read), and `GrantInvalid` for an
    unknown kind, predicate, item or gate (not in ``gates``), a count outside
    1..`[grants].max_count`, or no reason."""
    if why := AP.agent_marker(requested_agent):
        raise GrantRefused(AP.refusal(why))
    k = KINDS.get(kind)
    if k is None:
        raise GrantInvalid(f"unknown grant kind {kind!r}; the kinds are {', '.join(KINDS)}")
    if when and when not in k.whens:
        known = ", ".join(k.whens) or "none"
        raise GrantInvalid(f"{kind} has no predicate {when!r}; the predicates are {known}")
    cap = cfg.grants.max_count
    if not 1 <= count <= cap:
        raise GrantInvalid(f"count must be 1..{cap} ([grants].max_count); got {count}")
    if gate not in gates:
        raise GrantInvalid(f"no such gate {gate!r}; the gates are {', '.join(sorted(gates))}")
    if not reason.strip():
        raise GrantInvalid("a grant needs --reason: what the operator allowed and why")
    ttl = cfg.grants.ttl_h
    terms = {
        "item": item, "gate": gate, "kind": kind, "count": count, "reason": reason.strip(),
        "when": when,
        "expires": clock.iso_at(time.time() + ttl * 3600, timespec="seconds") if ttl > 0 else "",
    }  # fmt: skip
    with log.transaction():
        st = fold(log.read_all(), strict=False)
        if item not in st.items:
            raise GrantInvalid(f"no such item {item!r}")
        gid = _next_id(st)
        try:
            AP.grant(
                log, PREFIX + gid, content_digest(f"{gid}{sorted(terms.items())}"),
                note=reason.strip(), requested_agent=requested_agent, extra={"terms": terms},
            )  # fmt: skip
        except AP.ApprovalRefused as exc:
            raise GrantRefused(str(exc)) from exc
    return next(g for g in grants(fold(log.read_all(), strict=False)) if g.id == gid)


def pick(
    st: State,
    item: str,
    gate: str,
    force: bool = False,
    *,
    kind: str = "review_round",
    ctx: Ctx | None = None,
    now: float | None = None,
) -> Grant | None:
    """The grant a round past the budget may cite, or None.

    A STANDING grant applies when its predicate holds on ``ctx`` (a `None` ctx is not
    "true": nothing is assumed); a plain grant applies to a ``force``d round. Of those that
    apply, the oldest is taken first."""
    k = KINDS.get(kind)

    def applies(g: Grant) -> bool:
        if not g.standing:
            return force
        test = k.whens.get(g.when) if k else None
        return test is not None and ctx is not None and test(ctx)

    live = [
        g
        for g in grants(st)
        if (g.item, g.gate, g.kind) == (item, gate, kind) and g.usable(now) and applies(g)
    ]
    return live[0] if live else None


def describe(g: Grant, now: float | None = None) -> str:
    kind = KINDS.get(g.kind)
    unit = kind.unit if kind else g.kind
    tail = f" ({g.when})" if g.standing else ""
    state = "expired" if g.lapsed(now) else "spent" if not g.remaining else ""
    return (
        f"grant {g.id}: {g.count} {unit}{'' if g.count == 1 else 's'}, {g.used} used"
        f"{tail}{', ' + state if state else ''}"
    )


def lines(st: State, item: str, gate: str | None = None, now: float | None = None) -> list[str]:
    """`gate status` / `review` lines for the item's grants (one gate, or all)."""
    return [
        f"{describe(g, now)} -- {g.item}.{g.gate}, {g.by}, {g.reason}"
        for g in grants(st)
        if g.item == item and (gate is None or g.gate == gate)
    ]
