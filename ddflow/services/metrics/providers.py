"""The hook other services add metrics through, without editing this package.

``register(name, fn)`` adds a provider: ``fn(ctx)`` returns rows, each a mapping with
``metric``, ``group``, ``n`` (the samples behind it) and ``value`` (None when it cannot be
computed). The service applies the minimum-sample rule to them, so a provider gets it for
free: a row with ``n`` under ``[metrics] min_samples`` has its value withheld, never
reported as a number. A provider that raises is reported as unavailable, and the other
metrics are still shown.

``register_dimension(name, fn)`` adds a group-by dimension: ``fn(outcome, ctx)`` names the
group of one gate outcome (a guidance id, a goal id). The built-in dimensions are in
``DIMENSIONS``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ...config import Config
from ...core import progress as PR
from .facts import Facts, Outcome


@dataclass(frozen=True)
class Context:
    """What a provider may read: the facts, the config, and the view's window."""

    facts: Facts
    cfg: Config
    now: float
    since: float | None = None
    phase: str = ""

    def in_scope(self, item: str, at: float | None = None) -> bool:
        """Whether an item (and the time of what is being counted) is inside the view."""
        if self.since is not None and at is not None and at < self.since:
            return False
        if not self.phase or self.facts.state is None:
            return True
        return PR.phase_of(self.facts.state, item) == self.phase


Provider = Callable[[Context], Iterable[Mapping[str, Any]]]
Dimension = Callable[[Outcome, Context], str]

_PROVIDERS: dict[str, Provider] = {}
_DIMENSIONS: dict[str, Dimension] = {}


def register(name: str, fn: Provider, *, replace: bool = False) -> None:
    if name in _PROVIDERS and not replace:
        raise ValueError(f"metrics provider {name!r} is already registered")
    _PROVIDERS[name] = fn


def unregister(name: str) -> None:
    _PROVIDERS.pop(name, None)


def register_dimension(name: str, fn: Dimension, *, replace: bool = False) -> None:
    if name in DIMENSIONS or (name in _DIMENSIONS and not replace):
        raise ValueError(f"metrics dimension {name!r} is already registered")
    _DIMENSIONS[name] = fn


def unregister_dimension(name: str) -> None:
    _DIMENSIONS.pop(name, None)


def dimension_names() -> tuple[str, ...]:
    return (*DIMENSIONS, *sorted(_DIMENSIONS))


def _phase(o: Outcome, ctx: Context) -> str:
    return (PR.phase_of(ctx.facts.state, o.item) if ctx.facts.state else "") or "(none)"


def _kind(o: Outcome, ctx: Context) -> str:
    span = ctx.facts.spans.get(o.item)
    return span.kind if span else "(unknown)"


def _reviewer(o: Outcome, ctx: Context) -> str:
    """Who reviewed: only a review gate has a reviewer ('' leaves every other gate out)."""
    return (o.model or o.by or "(unknown)") if o.gate in PR.REVIEW_GATES else ""


#: The built-in group-by dimensions of a gate outcome. The reviewer is the model a review
#: recorded, else whoever recorded the verdict.
DIMENSIONS: dict[str, Dimension] = {
    "gate": lambda o, ctx: o.gate,
    "reviewer": _reviewer,
    "agent": lambda o, ctx: o.by or "(unknown)",
    "kind": _kind,
    "phase": _phase,
}


def key_for(dimension: str, o: Outcome, ctx: Context) -> str:
    fn = DIMENSIONS.get(dimension) or _DIMENSIONS[dimension]
    return fn(o, ctx)


def run_providers(ctx: Context) -> tuple[list[dict], dict[str, str]]:
    """(rows with the minimum-sample rule applied, {provider: why it gave none})."""
    rows: list[dict] = []
    failed: dict[str, str] = {}
    minimum = max(int(ctx.cfg.metrics.min_samples), 1)
    for name, fn in sorted(_PROVIDERS.items()):
        try:
            got = [dict(r) for r in fn(ctx)]
        except Exception as exc:  # a provider is someone else's code: its failure is a finding
            failed[name] = f"{type(exc).__name__}: {exc}"
            continue
        for row in got:
            row.setdefault("provider", name)
            n = row.get("n")
            if not isinstance(n, int) or n < minimum:
                row["value"] = None
                row["unavailable"] = f"{n if isinstance(n, int) else 0} samples, need {minimum}"
            rows.append(row)
    return rows, failed
