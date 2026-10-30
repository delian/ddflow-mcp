"""The swarm quality ledger: what each (family, model, task kind) has earned at the gates.

Pure and read-only. It folds the events once with ``metrics.facts.collect`` (the collector
``ddflow metrics`` and ``swarm_metrics`` use; there is no second one) and credits every
item completed in the window to the author who held it last:

* family and model: the lease's own ``model``, else the ``model`` of the author's latest
  ``session.started`` before the claim; family from ``config_sections.agent.family_for``.
  An author whose model cannot be told is keyed ``("", "")``, never guessed;
* kind: a ``kind:<word>`` tag on the item (free-form, so a security hunt or a drift probe
  can feed the same ledger), else the item's own kind.

Per cell: items, first-pass items (no gate failed before completion and none was
unavailable), rework (failed gates before completion), bugs filed within ``bug_window_s``
after completion, and gate counts. An ``unavailable`` or ``partial`` outcome is never a
pass: it sits in the denominator of ``gate_pass_rate`` and bars a first pass.

``explore`` is the routing hook (D-swarm-answers Q10): with probability ``share`` it names
the least-sampled candidate pair under ``min_n`` items. Defaults are module constants a
caller overrides per call; the swarm config section that sets them arrives with SW-score.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import NamedTuple

from ...config import Config
from ...config_sections.agent import family_for
from ...core import progress as PR
from ...core.events import Event
from ...core.model import State
from ..metrics.facts import collect

#: A bug filed this long after an item completed still counts against its author (a week).
BUG_WINDOW_S = 7 * 24 * 3600.0
#: Share of picks spent on under-measured pairs (Q10) and the sample count that ends it.
EXPLORE_SHARE = 0.10
EXPLORE_MIN_N = 5
#: Beta prior (successes, failures) behind ``Cell.score``; (1, 1) is "no opinion".
PRIOR = (1.0, 1.0)
KIND_TAG = "kind:"
_NOT_PASS = frozenset({"unavailable", "partial"})


class Key(NamedTuple):
    family: str
    model: str
    kind: str


@dataclass
class Cell:
    items: int = 0
    first_pass: int = 0
    unverified: int = 0  # items with no failure but a gate that was unavailable or partial
    rework: int = 0  # failed gates before completion
    bugs: int = 0  # bugs filed after completion, within the window
    gates_passed: int = 0
    gates_failed: int = 0
    gates_unavailable: int = 0  # unavailable and partial

    @property
    def first_pass_rate(self) -> float | None:
        return self.first_pass / self.items if self.items else None

    @property
    def gate_pass_rate(self) -> float | None:
        n = self.gates_passed + self.gates_failed + self.gates_unavailable
        return self.gates_passed / n if n else None

    def score(self, prior: tuple[float, float] = PRIOR) -> float:
        """Beta-smoothed first-pass rate: a thin cell stays near the prior mean."""
        a, b = prior
        return (self.first_pass + a) / (self.items + a + b)


@dataclass
class Ledger:
    cells: dict[Key, Cell]

    def cell(self, family: str, model: str, kind: str) -> Cell:
        return self.cells.get(Key(family, model, kind), Cell())

    def to_dict(self, prior: tuple[float, float] = PRIOR) -> list[dict]:
        rows = []
        for k, c in sorted(self.cells.items()):
            rows.append(
                {
                    **k._asdict(),
                    "items": c.items,
                    "first_pass": c.first_pass,
                    "first_pass_rate": c.first_pass_rate,
                    "unverified": c.unverified,
                    "rework": c.rework,
                    "bugs_after_merge": c.bugs,
                    "gate_pass_rate": c.gate_pass_rate,
                    "score": c.score(prior),
                }
            )
        return rows


def _models(events: Sequence[Event]) -> dict[str, list[tuple[float, str]]]:
    """agent -> [(time, model)] from ``session.started``, oldest first."""
    out: dict[str, list[tuple[float, str]]] = {}
    for ev in events:
        d = ev.data if isinstance(ev.data, dict) else {}
        if ev.kind == "session.started":  # a model-less session is a marker: it ends the old one
            m = d.get("model") if isinstance(d.get("model"), str) else ""
            out.setdefault(ev.agent, []).append((PR.epoch(ev.ts), m))
    for rows in out.values():
        rows.sort(key=lambda r: r[0])  # stable: equal times keep log order
    return out


def _lease_models(events: Sequence[Event]) -> dict[tuple[str, str], list[tuple[float, str]]]:
    """(item, holder) -> [(time, model)] per ``lease.acquired``; "" when it carried none."""
    out: dict[tuple[str, str], list[tuple[float, str]]] = {}
    for ev in events:
        d = ev.data if isinstance(ev.data, dict) else {}
        if ev.kind == "lease.acquired":
            m = d.get("model") if isinstance(d.get("model"), str) else ""
            out.setdefault((ev.subject, str(d.get("holder") or ev.agent)), []).append(
                (PR.epoch(ev.ts), m)
            )
    for rows in out.values():
        rows.sort(key=lambda r: r[0])
    return out


def _author_model(item: str, agent: str, claim: float, sessions: dict, leases: dict) -> str:
    """The model the author held at ``claim``: the lease's own, else their latest session's."""
    held = [m for t, m in leases.get((item, agent), ()) if t <= claim]
    if held and held[-1]:
        return held[-1]
    seen = [m for t, m in sessions.get(agent, ()) if t <= claim]
    return seen[-1] if seen else ""


def _kind(state: State, item: str, fallback: str) -> str:
    it = state.items.get(item)
    for tag in getattr(it, "tags", None) or ():
        if isinstance(tag, str) and tag.startswith(KIND_TAG) and tag[len(KIND_TAG) :].strip():
            return tag[len(KIND_TAG) :].strip()
    return fallback or "task"


def _credit(c: Cell, outcomes: Sequence, done: float, bug_times: Sequence[float], window: float):
    """Add one completed item's gate outcomes (up to completion) and later bugs to ``c``."""
    before = [o for o in outcomes if o.at <= done]
    failed = sum(o.kind == "failed" for o in before)
    weak = sum(o.kind in _NOT_PASS for o in before)
    c.items += 1
    c.rework += failed
    c.gates_failed += failed
    c.gates_unavailable += weak
    c.gates_passed += sum(o.kind == "passed" for o in before)
    c.first_pass += not failed and not weak
    c.unverified += not failed and bool(weak)
    c.bugs += sum(0 < t - done <= window for t in bug_times)


def build(
    events: Sequence[Event],
    state: State,
    cfg: Config,
    now: float,
    *,
    since: float | None = None,
    bug_window_s: float = BUG_WINDOW_S,
    families: dict[str, str] | None = None,
) -> Ledger:
    """The ledger over items completed at or after ``since`` (None: the whole log)."""
    facts = collect(events, state, now, cfg)
    sessions, leases = _models(events), _lease_models(events)
    fam_map = families if families is not None else dict(getattr(cfg.agent, "families", {}) or {})
    gates: dict[str, list] = {}
    for o in facts.outcomes:
        gates.setdefault(o.item, []).append(o)
    bugs: dict[str, list[float]] = {}
    for item, at in facts.bugs:
        bugs.setdefault(item, []).append(at)
    cells: dict[Key, Cell] = {}
    for span in facts.spans.values():
        done = span.completed_at
        if span.kind == "phase" or done is None or (since is not None and done < since):
            continue
        at, author = next(((t, a) for t, a in reversed(span.claims) if t <= done), (0.0, ""))
        model = _author_model(span.item, author, at, sessions, leases) if author else ""
        key = Key(family_for(model, fam_map or None), model, _kind(state, span.item, span.kind))
        c = cells.setdefault(key, Cell())
        _credit(c, gates.get(span.item, ()), done, bugs.get(span.item, ()), bug_window_s)
    return Ledger(cells)


def explore(
    ledger: Ledger,
    candidates: Sequence[tuple[str, str]],
    kind: str,
    *,
    draw: float,
    share: float = EXPLORE_SHARE,
    min_n: int = EXPLORE_MIN_N,
) -> tuple[str, str] | None:
    """The (family, model) to try instead of the best one, or None to exploit.

    ``draw`` is the caller's uniform [0, 1) sample (kept outside so a choice is replayable
    and loggable); it explores when ``draw < share``, picking the candidate with the fewest
    items for ``kind`` among those under ``min_n`` (ties by name).
    """
    if not draw < share:
        return None
    thin = sorted(
        ((ledger.cell(f, m, kind).items, f, m) for f, m in candidates),
    )
    return (thin[0][1], thin[0][2]) if thin and thin[0][0] < min_n else None
