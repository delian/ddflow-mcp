"""The progress report shown after every completion: where the whole queue stands, in a
few lines (`[session].progress_after_complete`, on by default).

The operator asked to always know the stage of the work without asking for it, at the
smallest token cost: counts and percentages from the folded state in one pass, no I/O,
and the next items the scheduler would offer.
"""

from __future__ import annotations

from ..config import Config
from ..core import schedule as S
from ..core.model import ABANDONED, DONE, State

MODES = ("on", "phase", "off")
#: How many ready items the report names as next.
NEXT_SHOWN = 3


def _pct(done: int, total: int) -> str:
    return f"{done}/{total} ({round(100 * done / total) if total else 100}%)"


def _live(st: State):
    return [it for it in st.items.values() if not it.removed and it.state != ABANDONED]


def _phase_counts(items, phase: str) -> tuple[int, int]:
    kids = [it for it in items if it.kind != "phase" and it.parent == phase]
    return sum(it.state == DONE for it in kids), len(kids)


def report(st: State, cfg: Config, item: str = "", *, mode: str = "") -> str:
    """The block, or "" when the mode is off. `item` names the item just completed: its
    phase is the one shown as current."""
    mode = mode or cfg.session.progress_after_complete
    if mode not in ("on", "phase"):
        return ""
    items = _live(st)
    lines: list[str] = []
    cur = st.items.get(item)
    phase = cur.id if cur is not None and cur.kind == "phase" else (cur.parent if cur else "")
    if mode == "on":
        work = [it for it in items if it.kind != "phase" and not it.fixes]
        bugs = [b for b in st.bugs.values() if b.resolution != "invalid"]
        fixed = sum(b.resolution == "fixed" for b in bugs)
        still = [b for b in bugs if b.open]
        severe = sum(b.severity in ("high", "critical") for b in still)
        phases = [it for it in items if it.kind == "phase"]
        closable = [
            p.id
            for p in phases
            if p.state != DONE and (c := _phase_counts(items, p.id))[1] and c[0] == c[1]
        ]
        lines.append(
            f"Progress: tasks {_pct(sum(it.state == DONE for it in work), len(work))}"
            f" · bugs fixed {_pct(fixed, len(bugs))}"
            + (f", {len(still)} open" + (f" ({severe} high)" if severe else "") if still else "")
            + f" · phases {_pct(sum(p.state == DONE for p in phases), len(phases))}"
            + (f", {len(closable)} ready to close" if closable else "")
        )
    if phase:
        done, total = _phase_counts(items, phase)
        lines.append(f"Phase {phase}: {_pct(done, total)}")
    ready = S.plan(st, cfg).ready[:NEXT_SHOWN]
    lines.append("Next: " + (", ".join(it.id for it in ready) if ready else "nothing ready"))
    return "\n".join(lines)
