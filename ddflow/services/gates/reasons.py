"""Why a gate did not pass, said where the status is read (B305db5608b)."""

from __future__ import annotations

WHY_CHARS = 200


def _unavailable_reasons(it, rows: list[tuple[str, str]]) -> dict[str, str]:
    """gate -> why it did not pass, for each `unavailable`, `partial` or `failed` outcome. The
    recorded reason is mandatory; a record from before that rule shows as having none, which
    is itself worth saying.

    A FAILED gate's reason was once dropped here (B-gate-status-hides-failed-reason): the
    queued-elapsed note won instead, so a real test failure read as a queue timeout."""
    out: dict[str, str] = {}
    for g, o in rows:
        if o not in ("unavailable", "partial", "failed"):
            continue
        rec = it.gates.get(g)
        text = " ".join(((rec.reason if rec else "") or "").split()) or "no reason was recorded"
        out[g] = text if len(text) <= WHY_CHARS else text[: WHY_CHARS - 3] + "..."
    return out
