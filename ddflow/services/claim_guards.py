"""Claim preflight guards: every reason a claim is refused before it asks for the lease.

A guard is a function ``(ClaimContext) -> Outcome | None`` registered under a name with an
``order``; `first_refusal` runs them in ``(order, name)`` order and returns the first refusal
(an `Outcome` is falsy when refused, so a result is compared to None, never chained with
``or``). A feature adds its refusal as a new ``claim_guard_<name>.py`` in `ddflow/api/lifecycle/` that
calls `register` at import: `claim.py` is not edited, and no two features touch one line.
Built-ins sit at orders 10, 20, 30; leave gaps.

Guards run on the claim's own state and must not write beyond the line/wait records a
refusal already makes (`_join_line`). A guard that cannot decide returns None.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core import outcome as O
from ..core.registry import Registry


@dataclass(frozen=True)
class ClaimContext:
    """What a guard sees: the folded state of the claim being attempted."""

    repo: Path
    cfg: Any
    events: Any
    st: Any
    item: str
    #: The globs the caller asked for (None: the item's own).
    want: Any
    #: The claiming agent.
    me: str
    force: bool = False
    #: The caller already holds a LIVE lease on this item (a re-claim only renews it).
    held_before: bool = False

    @property
    def skip(self) -> bool:
        """True for the usual ``--force`` or re-claim exemption."""
        return self.force or self.held_before


Check = Callable[[ClaimContext], "O.Outcome | None"]


@dataclass(frozen=True)
class Guard:
    name: str
    check: Check
    order: int = 50
    doc: str = ""


GUARDS: Registry[Guard] = Registry(
    "claim guard",
    key=lambda g: g.name,
    package="ddflow.api.lifecycle",  # the guards sit beside the helpers they use
    prefix="claim_guard_",
)


def register(name: str, order: int = 50) -> Callable[[Check], Check]:
    """Decorator: register ``check`` as claim guard ``name`` at ``order``."""

    def deco(check: Check) -> Check:
        GUARDS.register(Guard(name, check, order, (check.__doc__ or "").strip()))
        return check

    return deco


def ordered() -> list[Guard]:
    """Every guard, in the order they run."""
    return sorted(GUARDS.values_list(), key=lambda g: (g.order, g.name))


def first_refusal(ctx: ClaimContext) -> O.Outcome | None:
    """The first guard's refusal, or None when every guard lets the claim through."""
    for guard in ordered():
        refused = guard.check(ctx)
        if refused is not None:
            return refused
    return None
