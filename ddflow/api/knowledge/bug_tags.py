"""Tags on bug records: the bug carrier the label engine (P-labels) will wrap.

`get_tags` / `set_tags` are the get/set pair; `bug_tag` is `bug tag BUG --add --remove` over
it. The list arithmetic is the one items use (`core.globspec.adjust`), and the tags stay
plain strings -- a namespaced `ns:x` is one tag (D-labels). `wave:*` is derived by the
scheduler and reserved: it is never set by hand.
"""

from __future__ import annotations

from pathlib import Path

from ...core import globspec as GS
from ...core import outcome as O
from ...core.model import fold
from .._base import _load

RESERVED_PREFIXES = ("wave:",)


def get_tags(st, bug: str) -> list[str]:
    """The tags of ``bug`` in ``st`` ([] for an unknown bug)."""
    rec = st.bugs.get(bug)
    return list(rec.tags) if rec is not None else []


def reserved(tags) -> list[str]:
    """The tags among ``tags`` that are derived, not hand-set."""
    return [t for t in tags if t.startswith(RESERVED_PREFIXES)]


def found_tags(raw) -> tuple[list[str], str]:
    """``(tags, problem)`` for `bug found --tags`: the parsed list, or why it is refused."""
    tags = GS.parse(raw)
    bad = reserved(tags)
    return tags, (f"{', '.join(bad)}: the wave:* tags are derived and reserved." if bad else "")


def _apply(repo: Path, bug: str, change, *, agent: str, add=(), remove=()) -> O.Outcome:
    """``change(held)`` -> the new list, decided under the log lock from the log as it is."""
    log, _cfg, _st = _load(repo, agent)
    with log.transaction():
        st = fold(log.read_all(), strict=False)
        if bug not in st.bugs:
            return O.refused("bug.tagged", f"no bug {bug} is recorded in this log.", id=bug)
        was = get_tags(st, bug)
        tags = change(was)
        if was != tags:
            log.append("bug.tagged", bug, {"tags": tags, "add": list(add), "remove": list(remove)})
    return O.ok("bug.tagged", id=bug, tags=tags, was=was, changed=was != tags)


def _refusal(bug: str, tags) -> O.Outcome | None:
    bad = reserved(tags)
    if not bad:
        return None
    return O.refused(
        "bug.tagged",
        f"{', '.join(bad)}: the wave:* tags are derived by the scheduler and reserved.",
        id=bug,
    )


def set_tags(repo: Path, bug: str, tags: list[str], *, agent: str = "") -> O.Outcome:
    """Make ``tags`` the tag list of ``bug`` (open or closed): one `bug.tagged` event."""
    tags = GS.parse(tags)
    _log, _cfg, st = _load(repo, agent)
    held = get_tags(st, bug)
    # `set_tags(get_tags())` must round-trip: a derived tag the bug already holds is
    # accepted back; a NEW wave:* tag is refused (only the scheduler writes them)
    refused = _refusal(bug, [t for t in tags if t not in held])
    if refused is not None:  # an Outcome is falsy when it is not ok: test for None
        return refused
    return _apply(
        repo,
        bug,
        # the log as it is NOW decides the derived tags: the input never writes one
        lambda now: [*reserved(now), *(t for t in tags if t not in reserved(tags))],
        agent=agent,
    )


def bug_tag(
    repo: Path,
    bug: str,
    *,
    add: str | list[str] = "",
    remove: str | list[str] = "",
    agent: str = "",
) -> O.Outcome:
    """`bug tag BUG --add a,b --remove c`: adjust the bug's tags. Refused for an unknown
    bug or a reserved tag; failing when neither ``add`` nor ``remove`` is given."""
    add_l, rem_l = GS.parse(add), GS.parse(remove)
    if not add_l and not rem_l:
        return O.failed("bug.tagged", "give --add and/or --remove: there is nothing to change.")
    refused = _refusal(bug, [*add_l, *rem_l])
    if refused is not None:
        return refused
    return _apply(
        repo, bug, lambda held: GS.adjust(held, add_l, rem_l), agent=agent, add=add_l, remove=rem_l
    )
