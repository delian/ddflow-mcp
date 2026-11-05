"""Todo/box/heading scanning and section parsing helpers."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...config import Config
from ...core import ids as IDS
from ...core.globs import match as glob_match
from ...core.slug import slug as _slug
from ...core.slug import slug_words as _slug_words
from ._tables import (
    _ANY_HEADING,
    _SECTION,
    Found,
    _disposition,
    _fenced,
    _files,
    _parse_globs,
    _parse_needs,
    _push_heading,
    _scan_task_tables,
    _sections,
    _status_disposition,
    _unique,
)
from ._titles import (
    _CHECK,
    _GLOBS,
    _HEADING,
    _NEEDS,
    _OLD_PHASE_SLUG,
    _OLD_TASK_SLUG,
    _RESOURCES,
    TODO_GLOBS,
    _clean_title,
    _continuation,
    _cut_title,
    _prose_globs,
    _prose_needs,
    _split_id,
    _title_of,
    _titled,
)


def _box_disposition(
    body: str, stack: list[tuple[int, tuple[str, str] | None]], archived: bool
) -> tuple[str, str]:
    """An open box's disposition: its own marker, else its nearest heading's or section
    STATUS's, else -- in an archive file -- held until someone names the section."""
    kind, marker = _disposition(body)
    if kind:
        return kind, f"marked {marker}"
    inherited = next((d for _lvl, d in reversed(stack) if d is not None), None)
    if inherited and inherited[0]:
        return inherited
    if archived:
        return "hold", "in an archive file (`ddflow unblock <phase id>` releases a whole phase)"
    return "", ""


def _ids_config(repo: Path):
    """The project's config for the id templates an import mints with; the shipped
    defaults when it cannot be read (a proposal must not fail for it)."""

    try:
        return Config.load(repo)
    except Exception:
        return Config()


def scan_todos(
    repo: Path, globs: tuple[str, ...] = TODO_GLOBS, archive: tuple[str, ...] = ()
) -> tuple[list[Found], list[str]]:
    """Checklists → phases and tasks, grouped by the heading above them.

    A heading with checkboxes under it is a phase; each checkbox is a task. That is a
    convention rather than a law, which is exactly why the result is a *proposal* the
    agent reviews with the operator rather than something written straight to the log.
    """

    ids_cfg = _ids_config(repo)
    phase_chars = ids_cfg.importer.phase_slug_chars
    task_chars = ids_cfg.importer.task_slug_chars
    found: list[Found] = []
    empty: list[str] = []
    taken: set[str] = set()
    prose: list[tuple[Found, str]] = []
    for path in _files(repo, globs):
        rel = str(path.relative_to(repo))
        text = path.read_text("utf-8", errors="replace")
        # An ARCHIVE file's open boxes are history until someone names the section --
        # the source project's rule for its 20,000-line `docs/todo.md`, where no
        # classifier over the prose is trustworthy because "is this work?" is a property
        # of what the operator intends, not of the text. Held, not dropped: still
        # searchable, still resolvable as a dependency, and `ddflow unblock <phase>`
        # releases a whole section.
        archived = any(glob_match(rel, g, bare_any_depth=True) for g in archive)
        heading = ""
        heading_line = 0
        phase_ident = ""
        #: The phase this heading became, and its OWN `**STATUS**:` verdict -- which may
        #: sit above the first checkbox, before the phase exists.
        phase_found: Found | None = None
        heading_status: tuple[str, str] | None = None
        #: The task an annotation line attaches to. None until this file has produced
        #: one, so nothing leaks across a file or a heading boundary.
        anchor: Found | None = None
        before = len(found)
        #: `(level, disposition)` per open heading. None means "says nothing, inherit";
        #: `("", "")` is an explicit ACTIVE status, which overrides an ancestor's. A
        #: `## Session` STATUS line governs the `### 159.A` groups under it, and each of
        #: those is its own phase here, so the section's verdict has to be carried down.
        stack: list[tuple[int, tuple[str, str] | None]] = []
        lines = text.splitlines()
        fenced = _fenced(lines)
        for n, line in enumerate(lines, 1):
            if fenced[n - 1]:
                continue
            h = _HEADING.match(line)
            if h:
                heading = h.group(2).strip()
                heading_line = n
                phase_ident = ""
                phase_found = None
                heading_status = None
                anchor = None
                level = len(h.group(1))
                while stack and stack[-1][0] >= level:
                    stack.pop()
                _push_heading(stack, level, heading)
                continue
            m = _CHECK.match(line)
            if not m:
                status = _status_disposition(line)
                if status is not None and stack:
                    stack[-1] = (stack[-1][0], status)
                    heading_status = status
                    if phase_found is not None:
                        phase_found.extra["status"] = status
                    continue
                # Annotations live on the lines under their item -- and `anchor` is
                # reset per FILE, because `found` is not. A `**Globs:**` line at the top
                # of `b.md` was landing on the last task of `a.md`: a mis-attribution
                # invisible in the plan, since that task's `source` still points at its
                # own line, so the operator sees globs it never declared and no way to
                # tell where they came from.
                g = _GLOBS.search(line)
                if g and anchor is not None:
                    anchor.globs = _parse_globs(g.group(1))
                nd = _NEEDS.search(line)
                if nd and anchor is not None:
                    anchor.needs = _parse_needs(nd.group(1))
                rs = _RESOURCES.search(line)
                if rs and anchor is not None:
                    anchor.extra["resources"] = _parse_globs(rs.group(1))
                continue
            done = m.group(2).lower() == "x"
            body = m.group(3).strip()
            disposition = ("", "") if done else _box_disposition(body, stack, archived)
            if body.startswith("~~"):
                # The strike-through is the disposition, recorded above; the id inside it
                # is still the id every `Needs:` line and commit trailer uses.
                body = body.replace("~~", "").strip()
            # A TITLED id that is already taken falls back to the slug of the WHOLE line,
            # as it did before that shape was read: an id derived from a changed slug is
            # a new item to a project that imported the old one.
            unsplit = _clean_title(body) if _titled(body) else ""
            ident, body = _split_id(body)
            body = _clean_title(body)
            # The whole bullet: a sentence that wraps over several lines is ONE item, and
            # its text is what the queue must hold (B97ee75909a).
            more = _continuation(lines, fenced, n, len(m.group(1)))
            paragraph = _clean_title(f"{body} {more}") if more else body
            if heading and not phase_ident:
                # The heading's OWN id wins when it has one: `### 142.A — the
                # scaling-law advisor is wrong` is not a guess, it is the id the
                # project has been writing in commit trailers and in `Needs:` lines
                # for months. Slugging it to `142A-THE-SCALING-LAW-ADV` broke 39 of
                # the 47 declared dependencies in a real repository -- they pointed at
                # `142.A`, which then existed nowhere, and unknown dependencies are
                # treated as unmet, so the work imported permanently blocked.
                declared, rest = _split_id(heading)
                derived = IDS.render(  # the source's own spelling: not re-checked
                    ids_cfg,
                    "imported_phase",
                    check=False,
                    **{"user-text": _slug_words(heading, phase_chars).upper()},
                )
                old_derived = IDS.render(
                    ids_cfg,
                    "imported_phase",
                    check=False,
                    **{"user-text": _slug(heading, _OLD_PHASE_SLUG).upper()},
                )
                phase_ident = _unique(declared, derived, taken)
                phase_found = Found(
                    kind="phase",
                    ident=phase_ident,
                    title=_clean_title(rest if declared else heading),
                    source=f"{rel}:{heading_line}",
                    # Whether the id was READ or DERIVED. `_adopt_child_prefix`
                    # must not overrule a read one: `### 142.A` whose tasks are
                    # `142.1`, `142.2` has a child prefix of `142`, and taking it
                    # renamed the phase out from under every `Needs: 142.A` in the
                    # file.
                    extra={
                        "id_from_heading": bool(declared) and declared == phase_ident,
                        # Its own section STATUS, for `_phase_verdict`: every box
                        # ticked under "STATUS: PARTIAL" is not a finished phase.
                        "status": heading_status,
                        "heading_number": _heading_number(heading),
                        # The id this phase was given before ids were cut at word
                        # boundaries; "" when the heading declared its own.
                        "legacy_ident": "" if declared else old_derived,
                    },
                )
                found.append(phase_found)
            title = _title_of(paragraph)
            derived = IDS.render(
                ids_cfg,
                "imported_task",
                check=False,
                phase=phase_ident or "T",
                slug=_slug_words(unsplit or title, task_chars),
            )
            chosen = _unique(ident, derived, taken)
            anchor = Found(
                kind="task",
                ident=chosen,
                title=title,
                source=f"{rel}:{n}",
                done=done,
                body=paragraph if paragraph != title else "",
                globs=_prose_globs(paragraph),
                extra={
                    "old_slug": _slug(unsplit or body, _OLD_TASK_SLUG),
                    "phase": phase_ident,
                    # Whether the id was READ or DERIVED, and `_unique` decides that --
                    # a declared id that was already taken (two files carrying the same
                    # `**142.1**`, which `_unique` exists for) comes back as a slug.
                    # Recording it as source-read let it vote in `_adopt_child_prefix`,
                    # which is the circular vote that function's docstring forbids: it
                    # disagrees in the first component, empties the common prefix, and
                    # the phase silently keeps its prose slug. The phase branch above
                    # already guards this with `declared == phase_ident`.
                    "id_from_source": bool(ident) and chosen == ident,
                    # `closed` | `hold` | "" and the words that decided it, which become
                    # the blocked/abandoned reason -- a held item that cannot say why it
                    # is held is one nobody can decide to release.
                    "disposition": disposition[0],
                    "disposition_why": disposition[1],
                },
            )
            found.append(anchor)
            prose.append((anchor, paragraph))
        found.extend(_scan_task_tables(lines, fenced, rel, taken))
        if len(found) == before:
            empty.append(rel)
    _adopt_child_prefix(found, taken)
    _set_legacy_ids(found, ids_cfg)
    _add_prose_needs(found, prose)
    return found, empty


def _add_prose_needs(found: list[Found], prose: list[tuple[Found, str]]) -> None:
    """Add, after any `**Needs:**` line's ids, the ones each task's prose names."""
    known = {f.ident for f in found}
    for task, text in prose:
        task.needs.extend(d for d in _prose_needs(text, known, task.ident) if d not in task.needs)


def _set_legacy_ids(found: list[Found], ids_cfg) -> None:
    """Record, on every id this scan DERIVED, the id the same text got before ids were cut
    at word boundaries, so a re-import of a queue made then finds its items instead of
    importing them all again under new names (`_triage_found`)."""
    phases = {f.ident: f for f in found if f.kind == "phase"}
    seen: dict[str, int] = {}
    for f in found:
        old_slug = f.extra.pop("old_slug", None)
        if f.kind == "phase":
            legacy = "" if f.extra.get("renamed") else f.extra.get("legacy_ident", "")
        elif f.kind == "task" and old_slug is not None and not f.extra.get("id_from_source"):
            phase = phases.get(f.extra.get("phase", ""))
            # A renamed phase (`_adopt_child_prefix`) was renamed the same way then.
            old_phase = (phase.extra.get("legacy_ident") or phase.ident) if phase else "T"
            if phase and phase.extra.get("renamed"):
                old_phase = phase.ident
            legacy = IDS.render(
                ids_cfg, "imported_task", check=False, phase=old_phase, slug=old_slug
            )
        else:
            continue
        if legacy:
            # The old import numbered a repeat `-2`, `-3` in scan order (`_unique`).
            seen[legacy] = seen.get(legacy, 0) + 1
            if seen[legacy] > 1:
                legacy = f"{legacy}-{seen[legacy]}"
        if legacy and legacy != f.ident:
            f.extra["legacy_ident"] = legacy
        else:
            f.extra.pop("legacy_ident", None)


#: How many tasks must agree on a prefix before it outranks the heading the operator
#: actually wrote. One is a coincidence; two is a convention.
_PREFIX_QUORUM = 2


#: An ISO date anywhere in a heading or a filename. Journal entries carry theirs in
#: prose -- `Phase 98 closure — Engram Conditional Memory (2026-04-30)`, or leading:
#: `2026-04-22 — Phase 71: ...` -- and the file is usually `docs/log/2026-04.md`.
_DATE = re.compile(r"(?<!\d)(\d{4}-(?:0[1-9]|1[0-2])(?:-\d{2})?)(?!\d)")


def _date_hint(*texts: str) -> str:
    """The first ISO date found, searched in the order given. "" when there is none.

    Without this every imported journal entry is dated the day the import ran, so
    `recall` shows four hundred entries that all happened today and the chronology --
    the one thing a journal is FOR -- is gone. The heading is searched before the
    filename because `docs/log/2026-04.md` only narrows it to a month.
    """
    for t in texts:
        m = _DATE.search(t or "")
        if m:
            d = m.group(1)
            return d if len(d) == len("YYYY-MM-DD") else f"{d}-01"
    return ""


#: `## Phase 40 — Phase 34 follow-ups`: the number a heading gives its own section. A
#: separator must follow it -- `### Phase 39 follow-ups` is a heading ABOUT Phase 39,
#: and its items (`38.9`, `38.10`) are what the project calls Phase 38's leftovers.
_HEADING_NUMBER = re.compile(
    r"^\W*(?:phase|session|stage|milestone|sprint)\s+([A-Za-z]?\d[\w.]*?)"
    r"(?:\.\s|\.?(?:\s*[\u2014\u2013:(]|\s+-+\s|\s*\*\*|\s*$))",
    re.I,
)


def _heading_number(heading: str) -> str:
    """The number `Phase <N>` gives its section, "" when the heading names none."""
    m = _HEADING_NUMBER.match(heading)
    return m.group(1) if m else ""


def _agrees(prefix: str, number: str) -> bool:
    """Whether a child prefix spells the heading's own number -- `P21`, `21A`, `21.A`
    for `Phase 21`, `T4` for `Phase 4`, never `34` for `Phase 40` -- compared on the
    number in its first component."""

    def lead(ident: str) -> str:
        m = re.search(r"\d+", ident.split(".", maxsplit=1)[0])
        return (m.group(0).lstrip("0") or "0") if m else ""

    # A prefix with no number (`DRIVERFIX`, `B`) is the project's NAME for the phase,
    # not the id of another one: it agrees with any number.
    return not lead(prefix) or lead(prefix) == lead(number)


def _adopt_child_prefix(found: list[Found], taken: set[str]) -> None:
    """Rename a phase to the id prefix its own tasks already use.

    A heading reads `## Session DRIVERFIX — the defects the live runs exposed`, so the
    slug of it is `SESSION-DRIVERFIX-THE-DE` — while every task under it is
    `DRIVERFIX.1`, `DRIVERFIX.2`, and every commit trailer in the project's history
    says `Phase: DRIVERFIX`. Importing the slug produces a queue whose phase ids match
    nothing the project has ever written down, and the operator has to rename all of
    them by hand before any of it is recognisable.

    Only ids READ from the source vote. A task whose checkbox carried no id was given
    one derived from this phase's own slug, so counting it is circular — and it is
    worse than useless: one such task makes the ids disagree in their first component,
    the prefix comes out empty and the rename never happens. That is what kept
    `SESSION-DRIVERFIX-THE-DE` after five siblings had all said `DRIVERFIX`.

    A phase whose id was read from its own heading is left alone: the heading is
    evidence too, and a stronger one. `### 142.A` has children `142.1`, `142.2`, so the
    child prefix is `142` and taking it renames the phase out from under every
    `Needs: 142.A` in the file.

    So is a heading that NUMBERS itself: `## Phase 40 — Phase 34 follow-ups` holds
    `34.6e`, `34.8f`, and their prefix is the id of a different, closed phase. Such a
    phase takes its own number instead; a prefix that agrees with it (`P21.7` under
    `## Phase 21`) is still adopted, since it is the project's spelling of that number.
    """
    by_phase: dict[str, list[Found]] = {}
    for f in found:
        if f.kind == "task" and f.extra.get("phase"):
            by_phase.setdefault(f.extra["phase"], []).append(f)
    for phase in [f for f in found if f.kind == "phase"]:
        if phase.extra.get("id_from_heading"):
            continue
        kids = by_phase.get(phase.ident, [])
        voters = [t.ident for t in kids if t.extra.get("id_from_source")]
        best = _common_dotted_prefix(voters)
        if not best or len(voters) < _PREFIX_QUORUM:
            continue
        number = phase.extra.get("heading_number", "")
        if number and not _agrees(best, number):
            best = number
        if best in taken:
            continue
        old_ident = phase.ident
        taken.discard(old_ident)
        taken.add(best)
        for t in kids:
            t.extra["phase"] = best
            # A derived child id embeds the phase's old slug. Leaving it makes half the
            # queue read `DRIVERFIX.3` and the other half
            # `SESSION-DRIVERFIX-THE-DE.tidy-the-thing`, in the same phase.
            if not t.extra.get("id_from_source") and t.ident.startswith(f"{old_ident}."):
                taken.discard(t.ident)
                t.ident = _unique("", f"{best}.{t.ident[len(old_ident) + 1 :]}", taken)
        phase.ident = best
        phase.extra["renamed"] = True


def _common_dotted_prefix(idents: list[str]) -> str:
    """The longest dotted prefix every id shares, or "" if there is not one.

    Component-wise, not character-wise: `160.A.1` and `160.A.2` share `160.A`, and the
    whole point is that they must NOT collapse to `160` alongside `160.B.1` -- six
    sibling phases would then all want the id `160`, the first would take it and the
    other five would keep their prose slug. A queue with one recognisable phase id and
    five unrecognisable ones is worse than six unrecognisable ones, because it looks
    like it worked.
    """
    parts = [i.split(".") for i in idents if "." in i]
    if len(parts) < _PREFIX_QUORUM:
        return ""
    common: list[str] = []
    for col in zip(*parts, strict=False):
        if len(set(col)) != 1:
            break
        common.append(col[0])
    # Every component but the last: a prefix equal to a whole child id is not a prefix.
    while common and len(common) >= min(len(p) for p in parts):
        common.pop()
    return ".".join(common)


def _scan_sections(
    repo: Path,
    globs: tuple[str, ...],
    kind: str,
    ident: Callable[[str, str], str],
    extra: Callable[[str, str, str], dict[str, Any]] | None = None,
    body_chars: int = 2000,
) -> tuple[list[Found], list[str]]:
    """Every `##` section of every matching file, as `kind`.

    Lessons, research entries and journal entries differ in exactly three things — the
    glob, how the id is spelled, and what extra field the body yields — and had three
    copies of the same twelve-line loop around them. Three copies is three places to
    edit when the file-reading rule changes and three chances to miss one; it was
    already two versions of the empty-file rule before this. Adding a seventh source is
    now one three-line function.
    """
    found: list[Found] = []
    empty: list[str] = []
    for path in _files(repo, globs):
        rel = str(path.relative_to(repo))
        sections = _sections(path.read_text("utf-8", errors="replace"))
        if not sections:
            empty.append(rel)
            continue
        kept = [s for s in sections if not _is_index_section(s[0])]
        if not kept:
            empty.append(rel)
            continue
        for title, body, line in kept:
            found.append(
                Found(
                    kind=kind,
                    ident=ident(title, rel),
                    title=_cut_title(title),
                    source=f"{rel}:{line}",
                    body=body[:body_chars],
                    # bandit B610: `extra` is this function's own parameter, not Django's
                    # QuerySet.extra().
                    extra=extra(title, body, rel) if extra else {},  # nosec B610
                )
            )
    return found, empty


#: Titles of sections that are a table of contents rather than an entry.
_INDEX_TITLES = {"index", "contents", "table of contents", "toc"}


def _is_index_section(title: str) -> bool:
    return title.strip().strip("#*_ ").lower() in _INDEX_TITLES


#: A lesson heading that carries the project's own id: `### L100 (reinforces L99). Threads
#: and async share one GIL`. The id is what every cross-reference in the corpus (`[L147]`)
#: and every summary bullet cites, so it is kept rather than slugged away.
#: ONE id grammar, for the heading and for every citation of it: `L100`, `L100a`, and the
#: hyphenated `L-12` some corpora write. `L\d+` alone imported `## L-12 — ...` as the slug
#: `L-l-12-...`, so "see L-12" resolved to nothing and a summary bullet citing `(L-12)` was
#: filed as a second lesson instead of that lesson's summary (B-import-hyphen-ids).
_LESSON_ID = r"L-?\d+[a-z]?"
_LESSON_HEAD = re.compile(rf"^({_LESSON_ID})\b\s*(?:\([^)]{{0,80}}\))?\s*[.:—-]?\s*(.*)$")
_COMPRESSED = re.compile(r"\*\*Compressed:?\*\*:?\s*(.+?)(?:\n\s*\n|\Z)", re.S)
_SEEN_IN = re.compile(r"\*\*Seen in:?\*\*:?\s*(.+?)(?:\n\s*\n|\Z)", re.S)
#: Lessons are long; a 2,000-character cap kept roughly the first half of a typical entry
#: in the corpora this was measured on. The source file is committed anyway, so keeping the
#: whole rule costs nothing it does not already cost.
_LESSON_BODY_CHARS = 16000


def _lesson_level(text: str) -> int:
    """Non-zero when the corpus names its lessons with ids (`### L100.`); then
    `_id_entries` splits it, else the shallowest heading level does.

    A corpus that groups `### L100.` entries under `## <date> -- <context>` headings has
    its lessons below the top level, and splitting at the top imported 176 lessons as 24
    date-groups -- each "lesson" a day's worth of unrelated rules under a heading that
    states none of them.
    """
    counts: dict[int, int] = {}
    lines = text.splitlines()
    for ln, inside in zip(lines, _fenced(lines), strict=True):
        h = None if inside else _SECTION.match(ln)
        if h and _LESSON_HEAD.match(h.group(2).strip()):
            counts[len(h.group(1))] = counts.get(len(h.group(1)), 0) + 1
    return max(counts, key=counts.get) if counts else 0


def _id_entries(text: str) -> list[tuple[str, str, int]]:
    """`(title, body, line)` for every heading that carries a lesson id, AT ANY DEPTH.

    A single level chosen by majority vote dropped an id-bearing lesson that sat one
    level off -- a `## L200.` among `### L1xx.` entries vanished, neither merged nor
    reported (rubber-duck). Here each id-bearing heading starts an entry wherever it is;
    a heading WITHOUT an id at the entry's own depth or shallower is a container
    (`## <date> -- context`) and ends it, and a deeper one is part of its body.
    """
    lines = text.splitlines()
    fenced = _fenced(lines)
    out: list[tuple[str, str, int]] = []
    title: str | None = None
    level = 0
    buf: list[str] = []
    start = 0

    def flush() -> None:
        if title is not None:
            out.append((title, "\n".join(buf).strip(), start))

    for idx, ln in enumerate(lines, 1):
        # Level 1 included: a `# Appendix` after the last lesson is a container too, and
        # `_SECTION` (#{2,6}) could not see it, so it leaked into that lesson's body
        # (cross-family critic).
        h = None if fenced[idx - 1] else _ANY_HEADING.match(ln)
        if h:
            depth, heading = len(h.group(1)), h.group(2).strip()
            if _LESSON_HEAD.match(heading):
                flush()
                title, level, buf, start = heading, depth, [], idx
                continue
            if title is not None and depth <= level:
                flush()
                title = None
                continue
        if title is not None:
            buf.append(ln)
    flush()
    return out


def _one_paragraph(m: re.Match | None) -> str:
    return " ".join(m.group(1).split()) if m else ""


__all__ = [
    "_COMPRESSED",
    "_DATE",
    "_HEADING_NUMBER",
    "_INDEX_TITLES",
    "_LESSON_BODY_CHARS",
    "_LESSON_HEAD",
    "_LESSON_ID",
    "_PREFIX_QUORUM",
    "_SEEN_IN",
    "_add_prose_needs",
    "_adopt_child_prefix",
    "_agrees",
    "_box_disposition",
    "_common_dotted_prefix",
    "_date_hint",
    "_heading_number",
    "_id_entries",
    "_ids_config",
    "_is_index_section",
    "_lesson_level",
    "_one_paragraph",
    "_scan_sections",
    "_set_legacy_ids",
    "scan_todos",
]
