"""The full suite must have passed on a phase's merged tree before the phase closes
(D-gate-economy-r3 (a); an item's own gates run only the tests it reaches).

The pass is the `integration_tests` cadence run (`cadence.ran`), which a passing
`ddflow ci run --full` -- the merge queue's batch run included -- records. It counts when
it is NEWER than the last merge of any task under the phase."""

from __future__ import annotations

from collections.abc import Callable

from ..core.model import State

CADENCE = "integration_tests"


def last_merge_ts(events: list, st: State, phase: str) -> str:
    """The newest `worktree.merged` timestamp among the tasks under ``phase`` ("" if none)."""
    below = st.descendants(phase)
    return max(
        (e.ts for e in events if e.kind == "worktree.merged" and e.subject in below), default=""
    )


def last_pass_ts(st: State) -> str:
    return max((r["at"] for r in st.cadences.get(CADENCE, [])), default="")


def last_pass_sha(st: State) -> str:
    """The commit the newest recorded pass ran on ("" when it recorded none)."""
    runs = st.cadences.get(CADENCE, [])
    newest = max(runs, key=lambda r: r["at"], default=None)
    ev = newest.get("evidence") if newest else None
    return str(ev.get("sha") or "") if isinstance(ev, dict) else ""


def missing(
    events: list, st: State, phase: str, *, stands: Callable[[str], str] | None = None
) -> str:
    """ "" when a full-suite pass newer than the phase's last merge exists, else why not.

    ``stands(sha)`` (SV-revalidate-delta) is asked about an older pass that recorded the commit it
    ran on: it returns the note ('unchanged by delta') when main moved since without reaching any
    test, and then that pass still counts."""
    merged, passed = last_merge_ts(events, st, phase), last_pass_ts(st)
    if not merged or passed > merged:  # nothing merged under it: no tree to test
        return ""
    sha = last_pass_sha(st)
    if passed and sha and stands is not None and stands(sha):
        return ""
    since = f" since its last merge ({merged})"
    seen = f"last recorded pass {passed}" if passed else "none recorded"
    return (
        f"the full test suite has not passed on {phase}'s merged tree{since} ({seen}): run "
        f"`ddflow ci run --full` (records the pass), or set [schedule].phase_full_suite = 'run'"
    )
