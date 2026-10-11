"""Doctor's lines for events the fold could not apply (split out of `health`)."""

from __future__ import annotations

#: How many fold problems doctor names one by one; the rest are counted.
FOLD_PROBLEMS_SHOWN = 5


def fold_problem_notes(st) -> list[str]:
    """Doctor's lines for events the fold could not apply (`State.fold_problems`,
    B-uni-compat-events). A NOTE, like a skipped kind: the numbers doctor reports are
    computed without (the rest of) these events, and the log is append-only, so the remedy is a
    corrective event or a ddflow fix -- not something a re-run clears."""
    probs = list(getattr(st, "fold_problems", ()) or ())
    if not probs:
        return []
    lines = [
        f"{len(probs)} event(s) could not be folded: what each would have changed is "
        "missing from the counts below, and what its handler changed before it raised "
        "stays in (the log keeps them; a ddflow bug or a hand-edited shard -- file it with "
        "`ddflow bug found`):"
    ]
    lines += [
        f"  {p.event or '(no id)'} {p.kind} at lamport {p.lamport} by {p.agent or '?'}: {p.error}"
        for p in probs[:FOLD_PROBLEMS_SHOWN]
    ]
    if len(probs) > FOLD_PROBLEMS_SHOWN:
        lines.append(f"  ... and {len(probs) - FOLD_PROBLEMS_SHOWN} more")
    return ["\n".join(lines)]
