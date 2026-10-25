"""How long a recorded gate run queued elapsed (B1d51fb9da1), for `gate status`."""

from __future__ import annotations

from .reasons import _unavailable_reasons


def _queued_notes(it, rows) -> dict[str, str]:
    """gate -> how long its recorded run queued (host lock or admission slot), of its elapsed
    time. Only a run that recorded a positive ``waited_s``: an older log says nothing."""
    out: dict[str, str] = {}
    for g, _o in rows:
        rec = it.gates.get(g)
        ev = (rec.evidence or {}) if rec is not None else {}
        w, took = ev.get("waited_s"), ev.get("elapsed_s")
        if isinstance(w, int | float) and not isinstance(w, bool) and w > 0:
            of = f" of {_mins(took)}" if isinstance(took, int | float) else ""
            out[g] = f"queued {_mins(w)}{of} elapsed"
    return out


_MINUTE_S, _TENS_S, _TENTH_S = 60, 10, 0.1


def _mins(seconds) -> str:
    if seconds < _TENTH_S:
        return f"<{_TENTH_S}s"
    if seconds >= _MINUTE_S:
        return f"{seconds / _MINUTE_S:.0f}m"
    return f"{seconds:.0f}s" if seconds >= _TENS_S else f"{seconds:.1f}s"


def _why_notes(it, rows) -> dict[str, str]:
    """gate -> the note `gate status` shows beside it: its reason when unavailable or partial,
    else how long it queued (a reason wins: it is why the gate has no verdict)."""
    return {**_queued_notes(it, rows), **_unavailable_reasons(it, rows)}
