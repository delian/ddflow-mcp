"""Per-harness usage semantics: how each harness's usage figures READ, so a reader can
merge them without guessing (ORC-usage-semantics).

Three readings exist, and they must not be conflated:

* **cumulative** -- a running total per thread or session. Summing two of them, as Codex's
  ``turn.completed`` totals invite, double-counts. Merge by the **maximum**; the running
  figure only grows.
* **delta** -- a per-event increment. **Sum** them.
* **final** -- one final figure; take it as-is.
* **none** -- the harness exposes nothing, or its reader could not run: **unavailable**,
  never zero.

The declared facts live as data in ``ddflow/harnesses/usage.toml``. This module is the ONE
place that turns them into a merge, so B-quota-meters' max rule for Claude transcripts is
shared here rather than written a second time.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ddflow.adapters.toml import TomlLibIo

#: Reading modes. NONE is a real mode: it is how "this harness exposes nothing" is stated,
#: not an omission (r-tell-dont-discover).
NONE = "none"
CUMULATIVE = "cumulative"
DELTA = "delta"
FINAL = "final"
MODES: frozenset[str] = frozenset({NONE, CUMULATIVE, DELTA, FINAL})

INCLUDED = "included"
EXCLUDED = "excluded"
UNKNOWN = "unknown"
SUBAGENT_MODES: frozenset[str] = frozenset({INCLUDED, EXCLUDED, UNKNOWN})

_TEMPLATE = Path(__file__).resolve().parents[3] / "ddflow" / "harnesses" / "usage.toml"


class Unreadable(RuntimeError):
    """A harness with no declared, readable usage semantics. Reads as UNAVAILABLE."""


@dataclass(frozen=True)
class UsageSemantics:
    """What a harness declares about how its usage figures read."""

    harness: str
    mode: str
    subagents: str
    model_named_per_event: bool

    @property
    def readable(self) -> bool:
        return self.mode in (CUMULATIVE, DELTA, FINAL)


def load(source: str | Path | None = None) -> dict[str, dict]:
    """Read the declared usage semantics: harness family -> its declared facts."""
    path = Path(source) if source else _TEMPLATE
    return TomlLibIo().read(path)


def semantics_for(harness: str, source: str | Path | None = None) -> UsageSemantics:
    """The declared semantics for one harness family.

    An undeclared harness reads ``mode="none"`` (unavailable); a missing or corrupt
    table reads the same, never a guess and never zero.
    """
    try:
        facts = load(source)
    except (OSError, ValueError):
        # A missing file (OSError), bad TOML (TOMLDecodeError) or non-UTF-8 bytes
        # (UnicodeDecodeError) is the same read: unavailable.
        facts = {}
    if harness not in facts:
        return UsageSemantics(
            harness=harness, mode=NONE, subagents=UNKNOWN, model_named_per_event=False
        )
    declared = facts[harness]
    return UsageSemantics(
        harness=harness,
        mode=declared.get("mode", NONE),
        subagents=declared.get("subagents", UNKNOWN),
        model_named_per_event=bool(declared.get("model_named_per_event", False)),
    )


def merge_figures(mode: str, values: Iterable[float | int]) -> float | int | None:
    """Merge usages into ONE figure the way ``mode`` says.

    * ``cumulative`` -> the maximum (running totals only grow; summing double-counts);
    * ``delta`` -> the sum (each figure is an increment);
    * ``final`` -> the last, the one final total;
    * ``none`` -> ``None`` (the caller reports UNAVAILABLE, never zero).
    """
    vals = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if mode == NONE:
        return None
    if not vals:
        return None
    if mode == CUMULATIVE:
        return max(vals)
    if mode == DELTA:
        return sum(vals)
    if mode == FINAL:
        return vals[-1]
    raise ValueError(f"unknown usage mode {mode!r}; expected one of {sorted(MODES)}")


def resolve_figure(
    harness: str,
    values: Iterable[float | int],
    source: str | Path | None = None,
) -> float | int | None:
    """The merged figure for a harness, or ``None`` when it is unreadable."""
    semantics = semantics_for(harness, source)
    if semantics.mode == NONE:
        return None
    return merge_figures(semantics.mode, values)
