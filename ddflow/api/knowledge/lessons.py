"""Lessons: adding, verifying and searching what the project learned the hard way."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import ddflow.api._dedupe as DD

from ...config import csv_list
from ...core import ids as IDS
from ...core import outcome as O
from .._base import _load


def _store(repo, log, cfg):
    from ...infra.store import Store

    s = Store(repo, cfg)
    s.ensure(log)
    return s


@dataclass
class LessonDraft:
    """The lesson record, named once.

    Same reasoning as `decisions.Draft`: these fields are spelled out three times — the
    argparse flags, the MCP input schema and the event payload — and a field added to one is
    a field the other two silently drop. B20's `pattern` and `globs` are exactly such an
    addition, so they arrive as a type rather than as two more positional-ish keywords.

    `tags`, `seen_in`, `supersedes` and `globs` are comma-separated strings because that is
    what an argparse flag and a JSON string field both give you; `csv_list` is the ONE
    parser for that notation.
    """

    title: str
    rule: str = ""
    why: str = ""
    how: str = ""
    #: The one-paragraph form, rendered into `LESSONS-SUMMARY.md`.
    summary: str = ""
    tags: str = ""
    seen_in: str = ""
    supersedes: str = ""
    #: B20: the pattern this lesson forbids, and where to look for it. With a pattern, the
    #: repository is scanned AT FILING TIME and the matching sites are stored.
    pattern: str = ""
    globs: str = ""
    id: str = ""
    #: What the adder says about a possible duplicate (``_dedupe.Answer``).
    answer: DD.Answer | None = None


def lesson_add(repo: Path, draft: LessonDraft, *, agent: str = "") -> O.Outcome:
    """Record a transferable rule — the pattern, not the incident.

    With ``pattern``, the repository is SCANNED NOW and the matching sites are stored with
    the lesson (B20). That is the whole mechanism: `ddflow lesson verify` re-scans later
    and names which sites appeared, where a stored count could only say that things got
    worse. A count cannot be acted on and cannot be reviewed.
    """
    from ...services import inventory as INV

    log, cfg, st = _load(repo, agent)
    minted = IDS.mint(
        cfg, st, "lesson", events=log.read_all, given=draft.id, hash_parts=(draft.title, draft.rule)
    )
    lid = minted.id
    data: dict[str, Any] = {
        **IDS.key_field(minted),
        "title": draft.title,
        "rule": draft.rule,
        "why": draft.why,
        "how": draft.how,
        "tags": csv_list(draft.tags),
        "seen_in": csv_list(draft.seen_in),
        "supersedes": csv_list(draft.supersedes),
    }
    if draft.summary:
        data["summary"] = draft.summary
    sites: list[str] = []
    if draft.pattern:
        globlist = csv_list(draft.globs)
        try:
            sites = INV.scan(repo, draft.pattern, globlist)
        except INV.PatternError as exc:
            # REFUSED, not recorded with an empty inventory. A lesson whose pattern does not
            # compile would store zero sites, read as "the code is clean", and ratchet every
            # real occurrence away the first time anybody ran the check.
            return O.failed("lesson.pattern_invalid", str(exc), pattern=draft.pattern)
        data |= {"pattern": draft.pattern, "globs": globlist, "sites": sites}
    chk = DD.check_add(
        repo,
        log,
        cfg,
        st,
        DD.Record(
            kind="lesson",
            event_kind="lesson.recorded",
            rid=lid,
            title=draft.title,
            body="\n".join(x for x in (draft.rule, draft.why, draft.how) if x),
        ),
        draft.answer,
    )
    if chk.refusal is not None:
        return chk.refusal
    if chk.extension:
        return DD.extend(log, cfg, chk, "lesson.recorded")
    with log.transaction():
        minted = IDS.confirm(
            cfg, "lesson", minted, used=IDS.used_now(log), hash_parts=(draft.title, draft.rule)
        )
        log.append("lesson.recorded", lid, data | IDS.key_field(minted) | chk.fields)
        DD.after_add(log, cfg, lid, chk)
    return O.ok("lesson.recorded", id=lid, sites=len(sites), inventory=sites, **chk.data())


def lessons_verify(repo: Path, *, agent: str = "") -> O.Outcome:
    """Re-scan every lesson that declares a pattern, and name what reappeared (B20).

    Three exit codes, none collapsible:

    * ``0`` — every inventory still matches the code.
    * ``1`` — at least one forbidden pattern appeared at a NEW site. The sites are listed,
      because that is the whole point: a count says "worse" and never "which".
    * ``2`` — no lesson declares a pattern, so there is nothing mechanical to check. Not a
      pass: reporting "all clear" for a corpus with zero ratchets is how a project convinces
      itself it has checks it does not have.
    """
    from ...services import inventory as INV

    _log, _cfg, st = _load(repo, agent)
    live = {k: v for k, v in st.lessons.items() if not v.superseded_by}
    checkable = INV.checkable(st.lessons)
    if not checkable:
        nothing_checked = (
            f"No lesson declares a pattern, so nothing was checked ({len(live)} live "
            f"lesson(s)). Add `--pattern` to a lesson whose mistake is mechanical."
        )
        # `text` on EVERY branch: the tool declares `payload: "text"`, so a branch that
        # omits it raises KeyError out of `Outcome.body` rather than returning the exit-2
        # answer. Found by `test_a_migrated_tool_reproduces_its_CLI_json_exactly`, which
        # exercises each tool's wire shape — the exit-2 path had no other caller.
        return O.nothing(
            "lessons.verify", nothing_checked, checked=0, live=len(live), text=nothing_checked
        )
    diffs = [d for d in (INV.diff(repo, x) for x in checkable.values()) if d is not None]
    # Through `regressions()`, not a second copy of its filter: it owns "which lessons are
    # still in force and have come back", and a duplicate here would be a second answer to
    # the same question. (It WAS a duplicate, and the mutation run caught it: breaking
    # `regressions()` changed nothing because nothing called it.)
    regressed = INV.regressions(repo, checkable)
    data: dict[str, Any] = {
        "checked": len(diffs),
        "live": len(live),
        "regressed": [
            {"lesson": d.lesson, "appeared": d.appeared, "gone": d.gone} for d in regressed
        ],
        "fixed": [
            {"lesson": d.lesson, "gone": d.gone} for d in diffs if d.gone and not d.regressed
        ],
        "text": "\n".join(d.render() for d in diffs) or "nothing to report",
    }
    if regressed:
        return O.failed(
            "lessons.verify",
            f"{len(regressed)} lesson(s) have new sites: " + "; ".join(d.lesson for d in regressed),
            **data,
        )
    return O.ok("lessons.verify", **data)


def lesson_search(
    repo: Path, query: str, *, limit: int | None = None, agent: str = ""
) -> O.Outcome:
    log, cfg, _st = _load(repo, agent)
    hits = _store(repo, log, cfg).search(
        "lessons", query, limit if limit is not None else cfg.lessons.max_results
    )
    data: dict[str, Any] = {
        "hits": hits,
        "count": len(hits),
        "query": query,
        "snippet_chars": cfg.lessons.snippet_chars,
    }
    if not hits:
        return O.nothing("lesson.search", "no matching lessons", **data)
    return O.ok("lesson.search", **data)
