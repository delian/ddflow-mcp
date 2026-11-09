"""Why a gate did not pass, said where the status is read (B305db5608b)."""

from __future__ import annotations

import re

from ...core.clock import fmt_minute

WHY_CHARS = 200

#: A gate whose reason opens with the full-suite ``blocked on … since …`` line was bounced
#: when its wait for the host suite lock expired (the `SuiteBusy` reason, or the line `ci run`
#: prints and `classify_exit` turns into the reason). It reads as a LIVE queue entry long
#: after the wait ended, so `gate status` must also say when it was recorded and that the
#: wait is over (bug Bc53afb94b4).
_SUITE_WAIT = re.compile(r"^blocked on .+? since (?:\d{2}:\d{2}:\d{2}|an unknown time)")


def _past_suite_wait(text: str, at: str) -> str:
    """``text`` + a note that it is a FINISHED suite-lock timeout, not a live queue entry.

    ``at`` is when the unavailable outcome was recorded (the gate record's timestamp); a
    record without one still says the wait is past, it just cannot say when."""
    when = fmt_minute(at or "")
    if when:
        return f"{text} (recorded {when}; a past timeout -- re-run the gate)"
    return f"{text} (a past timeout -- re-run the gate)"


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
        if rec is not None and _SUITE_WAIT.match(rec.reason or ""):
            text = _past_suite_wait(text, rec.at)
        out[g] = text if len(text) <= WHY_CHARS else text[: WHY_CHARS - 3] + "..."
    return out
