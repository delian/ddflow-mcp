"""`Found`/`Duplicate`/`ImportPlan` and the Markdown-table task scan (`scan_todos`)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...core.ids import free
from ._titles import (
    _ANNOTATION_CHARS,
    _ASIDE_NOT_DISPOSITIONS,
    _CLOSED_MARKERS,
    _FUTURE_WORK,
    _HOLD_MARKERS,
    _LEAD_WORD_CHARS,
    _OPEN_ASIDE,
    _SECTION_CLOSED,
    _SECTION_HOLD,
    _SECTION_LIVE,
    _STATUS_CLOSED,
    _STATUS_HOLD,
    _STATUS_LINE,
    _STATUS_LIVE,
    _TITLE_ASIDE,
    _clean_title,
    _is_id,
    _marker_in,
    _split_id,
    _split_nonbold,
    _verdict_asides,
)


def _disposition(raw: str) -> tuple[str, str]:
    """`("closed" | "hold" | "", marker)` for one checkbox's text (after `[ ]`).

    A marker counts where it ANNOTATES the item -- after the title's bold run, or inside
    a parenthesised aside within the title -- and never in bare title prose:
    `**X.1 -- make the sampler handle SKIPPED batches**` is live work whose title merely
    mentions the word. A struck-through item (`~~...~~`) is closed.
    """
    body = raw.lstrip()
    if body.startswith("~~"):
        return "closed", "STRUCK THROUGH"
    # `**C11** consumable-triggered chains (... deferred notify) — 34.1`: the bold is only
    # the id and the TITLE follows it with no separator, so it is read like an unbolded
    # line, and a hold word in its asides must lead its clause (home-simulator's shape;
    # `**A.3** — a finding (DEFERRED)` keeps the separator and stays annotation).
    id_then_title = False
    if body.startswith("**"):
        close = body.find("**", 2)
        inner, rest = (body[2:close].strip(), body[close + 2 :]) if close != -1 else ("", "")
        id_then_title = (
            bool(inner)
            and " " not in inner
            and _is_id(inner)
            and bool(rest.strip())
            and not re.match(r"\s*[\u2014\u2013:(\-]", rest)
        )
        if id_then_title:
            body = rest.strip()
    if body.startswith("**"):
        close = body.find("**", 2)
        title, annotation = (body[2:close], body[close + 2 :]) if close != -1 else (body[2:], "")
    else:
        # No bold run to say where the title ends. Reading the WHOLE line as annotation
        # closed "Handle SKIPPED batches in the dataloader" -- live work, dropped
        # (cross-family critic). The title is the text before the first separator; a
        # disposition written as the leading word ("DECLINED: ...") still counts.
        title, annotation = _split_nonbold(body)
        lead = title.split(":", 1)[0].strip() if ":" in title[:_LEAD_WORD_CHARS] else ""
        # Only a lead that IS a marker ("DECLINED: ..."), never a phrase containing one:
        # "Retry REFUTED requests: add backoff" is work (roborev 835).
        if lead.upper() not in (*_CLOSED_MARKERS, *_HOLD_MARKERS):
            lead = ""
        annotation = f"{lead} {annotation}".strip()
    # Asides are judged WHOLE, before the window is cut: a cut inside one leaves it
    # unclosed, and its prose would count as a verdict.
    held_text = _verdict_asides(annotation, _HOLD_MARKERS)[:_ANNOTATION_CHARS]
    annotation = annotation[:_ANNOTATION_CHARS]
    # Closed asides, and one left OPEN at the end of the line -- a `(Deferred to ...`
    # whose closing paren sits on the item's next line.
    asides = [*_TITLE_ASIDE.findall(title), *_OPEN_ASIDE.findall(title)]
    aside = " ".join(asides)
    # A CLOSED word disposes anywhere in the annotation ("operator declined", "now
    # superseded by X.9") and in the title's asides; a HOLD word in an annotation aside
    # must lead its clause (`_verdict_asides`), and so must one in the title's asides
    # when the title is prose after an id-only bold.
    closed = _marker_in(annotation, _CLOSED_MARKERS) or _marker_in(aside, _CLOSED_MARKERS)
    if closed:
        return "closed", closed
    hold_aside = tuple(m for m in _HOLD_MARKERS if m not in _ASIDE_NOT_DISPOSITIONS)
    if id_then_title:
        aside = _verdict_asides(" ".join(f"({a})" for a in asides), hold_aside)
    hold = _marker_in(held_text, _HOLD_MARKERS) or _marker_in(aside, hold_aside)
    return ("hold", hold) if hold else ("", "")


def _heading_disposition(heading: str) -> tuple[str, str] | None:
    """What a heading says about its own section: a disposition, `("", "")` for an
    explicitly LIVE section (which overrides an ancestor's verdict), or None when it
    says nothing and the section inherits."""
    m = _marker_in(heading, _SECTION_CLOSED)
    if m:
        return "closed", f"under a {m} heading"
    m = _marker_in(heading, _SECTION_HOLD)
    if m:
        return "hold", f"under a {m} heading"
    if _marker_in(heading, _SECTION_LIVE):
        return "", ""
    if _FUTURE_WORK.match(_heading_title(heading)):
        # Not "under a ..." -- that prefix is what `_push_heading` refuses to let a live
        # sub-heading override, and a `(in progress)` item filed under "Future work" is
        # the operator saying this one has started.
        return "hold", "its heading files it as FUTURE WORK"
    return None


#: What a heading's title follows: `Phase 14 — `, `4. `, `P42.8 — `.
_HEADING_LEAD = re.compile(
    r"^\W*(?:(?:phase|session|stage|milestone|sprint)\s+[A-Za-z]?\d[\w.]*?(?:\.\s+|\s*[\u2014\u2013:\-]+\s*)"
    r"|\d+(?:\.\d+)*\.?\s+)",
    re.I,
)


def _heading_title(heading: str) -> str:
    """The heading with any id or `Phase N —` lead removed."""
    rest = _split_id(heading)[1] or heading
    return _clean_title(_HEADING_LEAD.sub("", _clean_title(rest)))


#: The reason a HEADING (not a STATUS line) gave: `under a DECLINED heading`.
_BY_HEADING = "under a "


def _push_heading(
    stack: list[tuple[int, tuple[str, str] | None]], level: int, heading: str
) -> None:
    """Push a heading's own verdict. A LIVE one overrides an ancestor's STATUS (`SHIPPED`
    over a section whose `(not started)` follow-ups are filed under it) but never an
    ancestor HEADING that declines or defers: `## Declined ideas / ### Idea A (not
    started)` is the operator's word about everything below it."""
    own = _heading_disposition(heading)
    if own == ("", ""):
        # The nearest blocking ancestor is PUSHED again, not merely inherited: a
        # "Future work" hold between it and this heading would otherwise be the
        # nearest verdict, and a declined section would come back as merely held.
        blocking = next(
            (d for _lvl, d in reversed(stack) if d and d[0] and d[1].startswith(_BY_HEADING)),
            None,
        )
        if blocking:
            own = blocking
    stack.append((level, own))


#: A negation in a STATUS verdict. Not an `UN-` prefix: `UNSHIPPED` already fails the
#: word-start guard in `_marker_in`, and matching `un` caught "DEFERRED until ...".
_NEGATED = re.compile(r"(?i)\b(NOT|NEVER|NO LONGER)\b|N'T\b")


def _status_disposition(line: str) -> tuple[str, str] | None:
    """The disposition a `**STATUS**:` line gives its section, or None if not one."""
    m = _STATUS_LINE.match(line)
    if not m:
        return None
    verdict = re.split(r"\s[—\-(]|[—(]", m.group(1).strip(), maxsplit=1)[0].strip()
    if _NEGATED.search(verdict):
        # `NOT DONE`, `NOT SHIPPED yet`: the word is there and the meaning is the
        # opposite. Read as closed, a section the operator marked unfinished imported
        # its open work as history (roborev 824). A negated verdict is live.
        return "", ""
    closed = _marker_in(verdict, _STATUS_CLOSED)
    if closed:
        live = _marker_in(m.group(1), _STATUS_LIVE)
        if live:
            # `CLOSED -- reopened in Phase 12, IN PROGRESS now`: a line extended in
            # place rather than rewritten. Dropping it as history lost live work; held,
            # it is visible and one `unblock` from work (rubber-duck).
            return "hold", (
                f"its section's STATUS says {closed} but also {live} -- ask the operator "
                f"which is true"
            )
        return "closed", f"its section's STATUS is {closed}"
    hold = _marker_in(verdict, _STATUS_HOLD)
    if hold:
        return "hold", f"its section's STATUS is {hold}"
    return "", ""


_SECTION = re.compile(r"^(#{2,6})\s+(.*)$")
_ANY_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
#: A fence line: ``` or ~~~, optionally indented and followed by an info string.
_FENCE = re.compile(r"^\s{0,3}(```+|~~~+)")


def _fenced(lines: list[str]) -> list[bool]:
    """Which lines sit inside a fenced code block (fence lines included).

    Structure is read only OUTSIDE fences. A `## PROMPT/NOTE` inside a ```console block
    in a real research log imported as a research entry of its own and carried away the
    addendum after it; a `- [ ]` inside a fenced example became a task. Inside a fence
    they are text, and they stay in the body of whatever entry the fence belongs to.

    A fence never CLOSED is treated as not a fence at all. CommonMark would run it to the
    end of the file, and did here: one typo swallowed every heading and checkbox after
    it, and because the file had yielded something earlier, nothing was reported
    (rubber-duck). A stray fence line costs one line of structure; the other reading
    costs the rest of the file.
    """
    out: list[bool] = []
    fence = ""
    opened = -1
    for i, ln in enumerate(lines):
        m = _FENCE.match(ln)
        if fence:
            out.append(True)
            # A closing fence carries no info string: "```py" opens a block, never
            # closes one.
            closes = m and not ln.strip()[len(m.group(1)) :].strip()
            if closes and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = ""
        elif m:
            fence = m.group(1)
            opened = i
            out.append(True)
        else:
            out.append(False)
    if fence:
        # Re-read everything after the stray opener as if it were not there, so a
        # properly paired fence further down is still a fence.
        out[opened + 1 :] = _fenced(lines[opened + 1 :])
    return out


def _sections(text: str) -> list[tuple[str, str, int]]:
    """`(title, body, first_line)` for every heading at the document's TOP level.

    The ONE splitter for lessons, research entries and journal entries -- all three are
    "a heading and the prose under it", and each scanner had grown its own loop: three
    copies of an off-by-one on the final section, and three chances to drift. The
    lessons copy built a `{title: line}` dict, so two sections sharing a title both
    reported the FIRST one's line number.

    **Only the shallowest level present splits.** A research entry is a `##` with `###`
    sub-headings under it -- "Sources", "Verdicts", "Known gaps" -- and splitting on
    every `#{2,6}` shredded one entry into five fragments, four of which were titled
    "Sources (opened, not snippet-cited)" and meant nothing on their own. Measured on a
    real corpus: 358 fragments where the file has ~90 entries.
    """
    lines = text.splitlines()
    fenced = _fenced(lines)
    heads = [None if f else _SECTION.match(ln) for ln, f in zip(lines, fenced, strict=True)]
    levels = [len(m.group(1)) for m in heads if m]
    if not levels:
        return []
    top = min(levels)
    out: list[tuple[str, str, int]] = []
    title, buf, start = "", [], 0
    for idx, ln in enumerate(lines, 1):
        h = heads[idx - 1]
        if not h or len(h.group(1)) != top:
            if title:
                buf.append(ln)
            continue
        if title:
            out.append((title, "\n".join(buf).strip(), start))
        title, buf, start = h.group(2).strip(), [], idx
    if title:
        out.append((title, "\n".join(buf).strip(), start))
    return out


#: The synthetic sessions imported notes land in: `(kind, session id, plural noun)`.
#: Named once because the writer and `verify_import` must agree on them -- a verifier
#: looking in the wrong session reports "nothing imported" about a full one, which is
#: the confident-wrong answer this module exists to avoid.
NOTE_SESSIONS: tuple[tuple[str, str, str], ...] = (
    ("journal", "s-imported-journal", "journal entr(ies)"),
)
#: Where memories imported by a version before `memory.recorded` existed landed. Still
#: counted by `import --verify`, so upgrading does not make an import look undone.
LEGACY_MEMORY_SESSION = "s-imported-memory"

#: Every kind a scanner can produce, in the order a human wants to read them. ONE list:
#: the CLI preview had its own and listed five of them, so a repository whose history is
#: a journal and a memory store printed a header with nothing under it. A new scanner
#: that forgets to extend this is caught by `tests/test_import_real_project.py`.
KINDS = (
    "phase",
    "task",
    # Not a scanned thing but an ACTION on one already in the queue: an imported phase
    # that `--include-done` finds finished. Its own kind so the preview lists it apart
    # from the phases being ADDED, and `--apply` writes a completion, never a re-add.
    "completion",
    "branch",
    "lesson",
    "decision",
    "research",
    "journal",
    "memory",
)


@dataclass
class Found:
    """One importable thing, with where it came from.

    `source` is not decoration. An imported queue that cannot be traced back is one
    nobody can check, and the first wrong item teaches the operator to distrust all of
    it.
    """

    kind: str  #: one of `KINDS`
    ident: str
    title: str
    source: str
    done: bool = False
    body: str = ""
    needs: list[str] = field(default_factory=list)
    globs: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Duplicate:
    """A found record left out of the proposal because it repeats an existing one."""

    found: Found
    of: str  #: the id of the record it repeats
    score: float  #: similarity, 0-1; 1.0 is the same text
    identical: bool = False
    #: `queue` (already in the log) or `import` (another record of this same import)
    where: str = "queue"


@dataclass
class ImportPlan:
    found: list[Found] = field(default_factory=list)
    #: Files that matched a glob but yielded nothing — reported, because "we looked and
    #: found nothing" and "we never looked" are different and only one needs action.
    empty_sources: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: Notes about the SOURCE FILES rather than the proposal (a file that yielded
    #: nothing). Kept apart so `verify_import`, which reports empty sources as a finding
    #: of its own, can leave them out by field rather than by matching prose.
    source_notes: list[str] = field(default_factory=list)
    #: Ticked tasks a plain import left out. A count the apply report states, because a
    #: board reading "0/3" for a phase whose other boxes shipped is otherwise read as
    #: "nothing shipped" (B45d5aa72fa).
    ticked_left_out: int = 0
    #: Memories over `[memory] max_chars` the plan left out (never cut): counted in the
    #: apply report as well as noted in the preview (B021a859d56).
    long_memories_left_out: int = 0
    #: Records the import did NOT propose because they repeat one already held: each
    #: `Duplicate` says which and how closely. Reported, never written -- the operator
    #: or the onboarding agent decides what to do with them (decision D-no-duplicates).
    duplicates: list[Duplicate] = field(default_factory=list)

    def by_kind(self, kind: str) -> list[Found]:
        return [f for f in self.found if f.kind == kind]

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.found:
            out[f.kind] = out.get(f.kind, 0) + 1
        return out


def _files(repo: Path, globs: tuple[str, ...]) -> list[Path]:
    """Every file matching any glob, each exactly once, in glob order.

    `TODO.md` and `todo.md` are two patterns and one file on a case-insensitive
    filesystem, so a plain concatenation scanned it twice and proposed every item in it
    twice -- which the id uniquifier then dutifully renamed to `-2` rather than
    dropping. Resolved paths, because a symlinked `docs/` is the same story.
    """
    out: list[Path] = []
    seen: set[Path] = set()
    for g in globs:
        for path in sorted(repo.glob(g)):
            key = path.resolve()
            if path.is_file() and key not in seen:
                seen.add(key)
                out.append(path)
    return out


#: How a `**Needs:**` line separates ids in the wild: commas, "and", "&", "+".
_NEEDS_SPLIT = re.compile(r"\s*(?:,|\band\b|&|\+)\s*")
#: Markdown that decorates an id inside a prose line: backticks, emphasis, brackets.
#: NOT the underscore -- `_ID` admits it (`\w` includes it), so `TASK_1` is a legal id
#: and stripping it here recorded a dependency on `TASK1`, which no item has. The two
#: halves of one grammar have to agree; `*` alone still covers `*emphasis*`.
_ID_DECOR = re.compile(r"[`*\[\]()\s]+")


#: Markdown that decorates a PATH: backticks and quotes. NOT `*` -- `src/**` is a real
#: glob and stripping its asterisks would silently turn a whole-subtree declaration into
#: a single file. That is why globs get their own undecorator instead of reusing the id
#: one, and why both live next to each other: the two halves of one grammar have to
#: agree about what is decoration, and they disagree about the asterisk on purpose.
_GLOB_DECOR = re.compile(r"^[`'\"\s]+|[`'\"\s]+$")


def _parse_globs(text: str) -> list[str]:
    """Paths from a `**Globs:** ...` line, undecorated.

    A backticked path folded to ``["`src/a.py`"]`` -- backticks and all -- and
    every consumer then compared that against a real path. `fnmatch` says no both ways
    round and so does the prefix fallback, so the task was NOT protected by the conflict
    detector while `ddflow show` printed a glob that read like a declaration and
    `import --verify` counted it as one.
    """
    out: list[str] = []
    for chunk in text.split(","):
        path = _GLOB_DECOR.sub("", chunk)
        if path and path not in out:
            out.append(path)
    return out


def _parse_needs(text: str) -> list[str]:
    """Ids from a `**Needs:** ...` line, undecorated and validated.

    Splitting on `,` alone and keeping whatever fell out produced dependencies on
    ``and`` and on ``164.F.2` `` -- a stray word and a trailing backtick. Neither
    exists, unknown dependencies are treated as unmet by design, and the items
    carrying them imported permanently blocked on a token nobody would ever create.
    Anything that is not id-shaped is dropped rather than guessed at.
    """
    out: list[str] = []
    for chunk in _NEEDS_SPLIT.split(text):
        token = _ID_DECOR.sub("", chunk).strip(".,;")
        if _is_id(token) and token not in out:
            out.append(token)
    return out


def _table_cells(row: str) -> list[str]:
    """The cells of a GFM table row: borders off, split on pipes that are not escaped,
    and `\\|` unescaped back to the pipe it was written as.

    A `|` is a column separator to every GFM reader, so a title is written with a
    literal pipe escaped (```ddflow seed plan\\|apply```, exactly what ``_cell`` in
    ``views/markdown`` writes) and a raw one is a column break. A bare
    ``line.split("|")`` here sends the title up to its first pipe and reads the
    leftover columns as globs -- titles cut and needs becoming globs that the
    conflict check then compares against real paths (B-sx-tasks-pipe-split-titles).
    """
    body = row.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    return [c.replace("\\|", "|") for c in re.split(r"(?<!\\)\|", body)]


#: The fields a task table declares, by the header word. An id and a title column are
#: what marks a row as a task table rather than prose; ``needs`` and ``globs`` are the
#: two a task carries. Unknown words are left out rather than guessed at.
_TABLE_FIELDS = ("id", "title", "needs", "globs")
_TABLE_HEAD = re.compile(r"[*_`\s]+")


def _table_columns(row: str) -> dict[str, int]:
    """``{field: index}`` of a task table's header row, or ``{}`` when the row names no
    id/title column (a heading table of graphs, a bug table...). Deliberately only the
    four words above: every alias the scanner guesses about is a wrong column later."""
    out: dict[str, int] = {}
    for i, cell in enumerate(_table_cells(row)):
        name = _TABLE_HEAD.sub("", cell).lower()
        if name in _TABLE_FIELDS and name not in out:
            out[name] = i
    return out


def _table_delim(cells: list[str]) -> bool:
    """A GFM delimiter row's cells are only ``-`` and ``:`` (``:---``), at least one of
    them, so a data row is told from how the table says a column may align and a blank
    line is a row END, not a delimiter (its cells are all empty)."""
    ones = [c.strip() for c in cells if c.strip()]
    return bool(ones) and all(re.fullmatch(r":?-+:?", c) for c in ones)


def _col(tbl: list[str], cols: dict[str, int], key: str) -> str:
    """The cell of column ``key``, or "" -- a GFM row may leave trailing cells off (the
    closing pipe and the empty cells before it), so a data row with no globs cell is read
    as having none rather than as an index past the row."""
    i = cols.get(key)
    return tbl[i] if i is not None and i < len(tbl) else ""


def _table_task(
    tbl: list[str], table_cols: dict[str, int], rel: str, n: int, taken: set[str]
) -> Found | None:
    """The task a ``| Id | Title | Needs | Globs |`` row becomes, or None for a row with no
    id and title to import. The Id cell is the id verbatim (a table that names an id says
    it exactly, where checkbox prose only shapes one); needs and globs are the columns
    that say so, so a title's own pipe -- written escaped -- stays IN the title instead of
    renumbering the columns (B-sx-tasks-pipe-split-titles)."""
    tid = _col(tbl, table_cols, "id").strip()
    title = _clean_title(_col(tbl, table_cols, "title"))
    if not tid or not title:
        return None
    tsk = Found(
        kind="task",
        ident=_unique(tid, "", taken),
        title=title,
        source=f"{rel}:{n}",
        body="",
        globs=_parse_globs(_col(tbl, table_cols, "globs")),
        extra={"id_from_source": True, "from_table": True},
    )
    if _col(tbl, table_cols, "needs"):
        tsk.needs = _parse_needs(_col(tbl, table_cols, "needs"))
    return tsk


def _scan_task_tables(
    lines: list[str], fenced: list[bool], rel: str, taken: set[str]
) -> list[Found]:
    """The tasks of every ``| Id | Title | Needs | Globs |`` table in ``lines``, in order.

    A header is a row that names an id/title column AND is followed by a delimiter row --
    the GFM rule that separates a header from a data row whose cell merely reads "id"
    (a header alone is data to a reader that mis-labels it). Task tables are a scan apart
    rather than more branches of ``scan_todos``; reading a row inside the checklist loop
    pushed that function past its lint ratchet for no added meaning. Kept in file order,
    so the id stays in ``taken`` beside the checkboxes that may share its name."""
    out: list[Found] = []
    cols: dict[str, int] = {}
    i = 0
    while i < len(lines):
        if fenced[i] or not lines[i].lstrip().startswith("|"):
            cols = {}
            i += 1
            continue
        header = _table_columns(lines[i])
        if (
            ("id" in header or "title" in header)
            and i + 1 < len(lines)
            and _table_delim(_table_cells(lines[i + 1]))
        ):
            cols = header
            i += 2  # step past the header and its delimiter row
            continue
        tbl = _table_cells(lines[i])
        if cols and tbl and not _table_delim(tbl):
            tsk = _table_task(tbl, cols, rel, i + 1, taken)
            if tsk is not None:
                out.append(tsk)
        i += 1
    return out


def _unique(preferred: str, fallback: str, taken: set[str]) -> str:
    """`preferred` if it is free, else `fallback`, else `fallback-2`, `-3`, ...

    Ids are the queue's primary key and two items sharing one is not a cosmetic
    problem: the second `task.added` folds over the first, so one of them vanishes and
    the import reports both as written. Two todo files legitimately reuse an id -- an
    open session and its archived predecessor -- so this has to be handled, not
    asserted away.
    """
    for candidate in (preferred, fallback):
        if candidate and candidate not in taken:
            taken.add(candidate)
            return candidate
    base = fallback or preferred or "item"
    got = free(f"{base}-2", taken, numbered=lambda k: f"{base}-{k}", start=3)
    taken.add(got)
    return got


__all__ = [
    "KINDS",
    "LEGACY_MEMORY_SESSION",
    "NOTE_SESSIONS",
    "_ANY_HEADING",
    "_BY_HEADING",
    "_FENCE",
    "_GLOB_DECOR",
    "_HEADING_LEAD",
    "_ID_DECOR",
    "_NEEDS_SPLIT",
    "_NEGATED",
    "_SECTION",
    "_TABLE_FIELDS",
    "_TABLE_HEAD",
    "Duplicate",
    "Found",
    "ImportPlan",
    "_col",
    "_disposition",
    "_fenced",
    "_files",
    "_heading_disposition",
    "_heading_title",
    "_parse_globs",
    "_parse_needs",
    "_push_heading",
    "_scan_task_tables",
    "_sections",
    "_status_disposition",
    "_table_cells",
    "_table_columns",
    "_table_delim",
    "_table_task",
    "_unique",
]
