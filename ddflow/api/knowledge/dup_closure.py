"""Closing a duplicate bug: only once its original is fixed, and citing the original's test.

D-no-duplicates (3): nothing closes automatically. `bug fixed X` lists the open bugs linked
to X as duplicates (`open_duplicates`); each is then closed explicitly -- `bug duplicate D
--of X` here, or by its own test, or `bug invalid`. A duplicate is a report of a defect the
original's regression test already guards, so closing one without that fix would hide it.
"""

from __future__ import annotations

from pathlib import Path

from ...core import outcome as O
from .._base import _load
from .bug_void import bug_invalid
from .bugs import _unknown_bug
from .pairs import link_record


def open_duplicates(st, bug: str) -> list[str]:
    """Open bugs that say they are duplicates of ``bug`` (the link sits on the duplicate)."""
    found = {
        other for other, lk in st.links.items() if other != bug and bug in lk.linked("duplicate_of")
    }
    return sorted(b for b in found if b in st.bugs and b != bug and st.bugs[b].open)


def bug_duplicate(repo: Path, bug: str, *, of: str, agent: str = "") -> O.Outcome:
    """Close ``bug`` as a duplicate of ``of``, which must be a FIXED bug with a test.

    Refused (exit 3): an unknown id, itself, a bug already closed, an original still open
    or closed as invalid, or fixed with no regression test recorded.
    """
    _log, _cfg, st = _load(repo, agent)
    if bug not in st.bugs:
        return _unknown_bug("bug.duplicate", bug, st)
    if of not in st.bugs:
        return _unknown_bug("bug.duplicate", of, st)
    if bug == of:
        return O.refused("bug.duplicate", f"{bug} cannot be a duplicate of itself", id=bug)
    if not st.bugs[bug].open:
        return O.refused("bug.duplicate", f"bug {bug} is already closed", id=bug)
    orig = st.bugs[of]
    if orig.resolution != "fixed":
        return O.refused(
            "bug.duplicate",
            f"bug {of} is not fixed yet ({orig.resolution or 'open'}): a duplicate is closed "
            f"only once its original is fixed. Fix {of} first, or keep {bug} open.",
            id=bug,
        )
    test = (orig.regression_test or "").strip(" ,;")
    if not test:
        return O.refused(
            "bug.duplicate",
            f"bug {of} was fixed with no regression test recorded; there is nothing to cite "
            f"for {bug}. Close {bug} with its own test (`bug fixed`) instead.",
            id=bug,
        )
    link = link_record(repo, bug, "duplicate_of", of, reason="bug duplicate", agent=agent)
    if not link.ok:
        return link
    out = bug_invalid(
        repo,
        bug,
        reason=f"duplicate of {of}, fixed and guarded by {test}",
        evidence=test,
        agent=agent,
    )
    if not out.ok:
        return out
    return O.ok(
        "bug.duplicate",
        id=bug,
        of=of,
        regression_test=test,
        **{k: v for k, v in out.data.items() if k.startswith("fix_task")},
    )
