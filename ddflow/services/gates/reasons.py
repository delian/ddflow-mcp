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


def _past_note(at: str) -> str:
    """The note that a full-suite timeout is FINISHED, not a live queue entry (bug Bc53afb94b4).

    ``at`` is when the unavailable outcome was recorded (the gate record's timestamp); a
    record without one still says the wait is past, it just cannot say when."""
    when = fmt_minute(at or "")
    return (
        f"recorded {when}; a past timeout -- re-run the gate"
        if when
        else "a past timeout -- re-run the gate"
    )


def _suite_wait_note(text: str, at: str) -> str:
    """``text`` with its past-timeout note, the note kept WHOLE when the reason is long.

    A holder line can embed a filesystem path, so ``text`` alone may already approach
    `WHY_CHARS`; the generic front-slice would then cut the note off the TAIL and leave only
    the present-tense wording -- the exact misread this note exists to fix. Here the REASON
    is cut instead, never the note."""
    note = _past_note(at)
    tail = f" ({note})"
    if len(text) + len(tail) <= WHY_CHARS:
        return text + tail
    head = text[: WHY_CHARS - len(tail) - 3].rstrip() + "..."
    return head + tail


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
        reason = (rec.reason if rec else "") or ""
        text = " ".join(reason.split()) or "no reason was recorded"
        # Match the COLLAPSED text: `split()` already folds `\r`/`\n` inside the reason to
        # ordinary spaces, and the regex's `.` does not cross a newline, so matching the raw
        # string missed a reason whose "since <time>" was preceded by a newline.
        if rec is not None and _SUITE_WAIT.match(text):
            text = _suite_wait_note(text, rec.at)
        out[g] = text if len(text) <= WHY_CHARS else text[: WHY_CHARS - 3] + "..."
    return out
