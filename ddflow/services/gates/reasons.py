"""Why a gate did not run, said where the status is read (B305db5608b)."""

from __future__ import annotations

WHY_CHARS = 200


def _unavailable_reasons(it, rows: list[tuple[str, str]]) -> dict[str, str]:
    """gate -> why it did not run, for each `unavailable` or `partial` outcome. The recorded
    reason is mandatory; a record from before that rule shows as having none, which is itself
    worth saying."""
    out: dict[str, str] = {}
    for g, o in rows:
        if o not in ("unavailable", "partial"):
            continue
        rec = it.gates.get(g)
        text = " ".join(((rec.reason if rec else "") or "").split()) or "no reason was recorded"
        out[g] = text if len(text) <= WHY_CHARS else text[: WHY_CHARS - 3] + "..."
    return out
