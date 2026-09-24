"""Import an existing project's history so Orchard can continue it, not restart it.

A project that adopts Orchard on day 400 has four hundred days of work already: a todo
file with things done and things not, a lessons corpus, architecture decisions, bugs
fixed, branches half-landed. `orchard adopt` used to start the queue **empty**, which
is the worst possible answer — the tool then reports "nothing in flight" to a project
with six things in flight, and an agent believes it.

Two halves, and the split is the whole design:

**This module does what it can VERIFY.** A checkbox is a fact: `- [x]` means someone
ticked it. A `## ` heading in a lessons file is a lesson. A file under `docs/adr/` is a
decision. A branch with unmerged commits is work in flight. Those are parsed, attributed
to their source line, and offered.

**The agent does what needs JUDGEMENT**, guided by the `import-existing-project`
workflow prompt: which headings are phases and which are tasks, what depends on what,
which globs each touches, whether a five-year-old lesson still applies. That half is not
automatable and pretending otherwise produces a confident, wrong queue — and a wrong
queue is worse than no queue, because the scheduler will hand it out.

Three rules hold this together:

1. **Dry-run by default.** `plan_import` reads and reports; `apply_import` writes, and
   only when asked. Nothing is imported as a side effect of looking.
2. **Every item carries its source.** `docs/todo.md:41` goes in the body, so the
   operator can check the import and the next reader can find the original.
3. **Idempotent.** Ids are derived from the source, so re-running after editing the
   todo adds what is new and leaves the rest alone. An import you cannot re-run is an
   import you have to get right first time.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..infra import proc as P
from ..infra.log import EventLog

#: Where projects actually keep these things. Ordered so the most specific wins when a
#: repository has several; every match is reported, none is guessed between.
TODO_GLOBS = (
    "docs/todo/open/*.md",
    "docs/todo/archive/*.md",
    "docs/todo.md",
    "tasks/todo.md",
    "TODO.md",
    "todo.md",
    "docs/TODO.md",
    "docs/plan.md",
    "PLAN.md",
    "docs/roadmap.md",
    "ROADMAP.md",
)
LESSON_GLOBS = (
    "docs/lessons.md",
    "docs/lessons-learned.md",
    "LESSONS.md",
    "docs/LESSONS.md",
    "docs/retrospectives/*.md",
)
#: A heading that says the work is finished, in the spellings projects actually use.
#: Checked against the PHASE heading only: a phase marked shipped whose tasks are still
#: unticked is the single most valuable thing this scan can tell the operator, because
#: it is drift they cannot see and the queue would otherwise hand that work out again.
_DONE_MARKER = re.compile(r"\bSHIPPED\b|\bCLOSED\b|\bDONE\b|\bCOMPLETE[D]?\b|✅", re.I)
#: Files inside an ADR directory that are the index rather than a decision.
_DECISION_INDEX_STEMS = {"readme", "index", "template", "0000-template", "_template"}
DECISION_GLOBS = (
    "docs/adr/*.md",
    "docs/decisions/*.md",
    "doc/adr/*.md",
    "adr/*.md",
    "docs/architecture/decisions/*.md",
)
RESEARCH_GLOBS = ("docs/RESEARCH.md", "docs/research.md", "RESEARCH.md")
#: OptMem and the tools that copied its layout: an append-only `LOG.txt` of fixed-width
#: one-line records. The cross-session memory an agent built up over months, which is
#: precisely the thing a fresh queue would otherwise throw away.
OPTMEM_GLOBS = (
    ".agent_memory/LOG.txt",
    ".memo/LOG.txt",
    ".optmem/LOG.txt",
    "agent_memory/LOG.txt",
)
#: `#41 2026-09-14 the text of the memory`, right-padded to the record width.
_OPTMEM = re.compile(r"^#(\d+)\s+(\d{4}-\d{2}-\d{2})\s+(.*?)\s*$")
#: The engineering journal — what happened, when, and why. Most projects keep one and
#: nothing in Orchard could read it, so `recall` could answer "what did we decide" and
#: not "what happened in March".
JOURNAL_GLOBS = (
    "docs/log/*.md",
    "docs/journal/*.md",
    "docs/CHANGELOG.md",
    "CHANGELOG.md",
    "docs/log.md",
    "JOURNAL.md",
)

#: Every file pattern the scanners read, in one tuple. `_instruction_vars` counts these
#: to decide whether to OFFER the import at handshake time, and it counted three of the
#: seven families — so a project whose entire history is an engineering journal and a
#: memory store was told nothing, which is precisely the case the offer exists for.
SOURCE_GLOBS = (
    TODO_GLOBS + LESSON_GLOBS + DECISION_GLOBS + RESEARCH_GLOBS + JOURNAL_GLOBS + OPTMEM_GLOBS
)

#: `- [ ] **P1.T2** — do the thing` / `- [x] do the thing`
_CHECK = re.compile(r"^(\s*)[-*]\s+\[( |x|X)\]\s+(.*)$")
#: The id, when the project already uses one. Three spellings, and the difference
#: between them is the whole reason this is not one pattern:
#:
#:   DELIMITED — `**P2.T1**` or `[P3.T1]`: the delimiters say "this is an id", so no
#:               separator is needed after it.
#:   BARE      — `P2.T1 — text`: nothing marks it as an id except the separator, so the
#:               separator is REQUIRED. Without that rule `fix v2 parsing` loses its
#:               first two words to a title that was never an id.
#:   SPANNING  — `**WFOPT.5.2 (DECISION) — DECLINED by the operator**`, where the bold
#:               wraps the id AND the description. Found only by running this against a
#:               real 4,799-item todo file: the DELIMITED pattern needs `**` right after
#:               the id and did not match, the BARE pattern is anchored at `^` and the
#:               `**` blocked it, so every one of these imported under a slug derived
#:               from its own prose — `SESSION-DRIVERFIX-THE-DE.driverfix1-step-1-pi`
#:               instead of `DRIVERFIX.1`. A queue whose ids do not match the ids the
#:               project has been using in commit trailers for months is not an import.
#:
#: The optional `(...)` is an annotation projects put between the id and the separator
#: ("(DECISION)", "(BLOCKED)"); it is bounded so it cannot swallow a sentence.
#: A permissive TOKEN. What counts as an id is decided by `_is_id`, in code, because
#: the rule is a conjunction and expressing it in the regex made it unreadable and
#: wrong: the pattern required an id to start with a LETTER, so `160.A` and `142.A` --
#: the shape this project has used for two hundred phases -- were not ids at all.
_ID = r"[A-Za-z0-9][\w.\-]*"
_ITEM_ID = re.compile(
    r"^(?:"
    rf"\*\*({_ID})\*\*|"
    rf"\[({_ID})\]"
    r")\s*[—\-:]?\s*"
    rf"|^(?:\*\*)?({_ID})(?:\s*\([^)]{{0,30}}\))?\s*[—:]\s+"
)


def _is_id(token: str) -> bool:
    """An id carries a digit, and is either dotted or starts with a letter.

    The digit is what separates `P2.T1` from a title beginning with a word. The second
    clause is what lets `160.A` in -- dotted, so the structure itself says "id" -- while
    keeping `3` and `2026` out of a list that starts with a number.
    """
    return bool(token) and any(c.isdigit() for c in token) and ("." in token or token[0].isalpha())


def _split_id(text: str) -> tuple[str, str]:
    """`(ident, remaining text)`. `("", text)` when the line carries no id."""
    m = _ITEM_ID.match(text)
    if not m:
        return "", text
    token = m.group(1) or m.group(2) or m.group(3) or ""
    if not _is_id(token):
        return "", text
    return token, text[m.end() :]


#: Markdown emphasis left over once the id is gone. A title reading
#: `step 1 picks operator-DEFERRED work.** "First ...` is the raw source line, not a
#: title, and it is what every board, every `next` and every gate prompt would show.
#: All of them, not just the edges: an id inside a spanning bold leaves the CLOSING
#: `**` in the middle of the title.
_BOLD = re.compile(r"\*\*")


def _clean_title(text: str) -> str:
    return _BOLD.sub("", text.strip()).strip()


#: `**Globs:** a/**, b/*` and `**Needs:** X, Y` — annotations this project writes and
#: several others copy. Absent in most repositories, which is fine: the agent adds them.
_GLOBS = re.compile(r"\*\*Globs?:?\*\*:?\s*(.+)", re.I)
_NEEDS = re.compile(r"\*\*Needs?:?\*\*:?\s*(.+)", re.I)
#: A markdown heading, used to group checkboxes into phases.
_HEADING = re.compile(r"^(#{1,4})\s+(.*)$")


_SECTION = re.compile(r"^(#{2,6})\s+(.*)$")


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
    levels = [len(m.group(1)) for m in (_SECTION.match(ln) for ln in text.splitlines()) if m]
    if not levels:
        return []
    top = min(levels)
    out: list[tuple[str, str, int]] = []
    title, buf, start = "", [], 0
    for idx, ln in enumerate(text.splitlines(), 1):
        h = _SECTION.match(ln)
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


#: Every kind a scanner can produce, in the order a human wants to read them. ONE list:
#: the CLI preview had its own and listed five of them, so a repository whose history is
#: a journal and a memory store printed a header with nothing under it. A new scanner
#: that forgets to extend this is caught by `tests/test_import_real_project.py`.
KINDS = (
    "phase",
    "task",
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
class ImportPlan:
    found: list[Found] = field(default_factory=list)
    #: Files that matched a glob but yielded nothing — reported, because "we looked and
    #: found nothing" and "we never looked" are different and only one needs action.
    empty_sources: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def by_kind(self, kind: str) -> list[Found]:
        return [f for f in self.found if f.kind == kind]

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.found:
            out[f.kind] = out.get(f.kind, 0) + 1
        return out


def _slug(text: str, limit: int = 48) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    s = re.sub(r"[\s_-]+", "-", s)
    return s[:limit].strip("-") or "item"


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
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    taken.add(f"{base}-{n}")
    return f"{base}-{n}"


def scan_todos(repo: Path) -> tuple[list[Found], list[str]]:
    """Checklists → phases and tasks, grouped by the heading above them.

    A heading with checkboxes under it is a phase; each checkbox is a task. That is a
    convention rather than a law, which is exactly why the result is a *proposal* the
    agent reviews with the operator rather than something written straight to the log.
    """
    found: list[Found] = []
    empty: list[str] = []
    taken: set[str] = set()
    for path in _files(repo, TODO_GLOBS):
        rel = str(path.relative_to(repo))
        text = path.read_text("utf-8", errors="replace")
        heading = ""
        heading_line = 0
        phase_ident = ""
        #: The task an annotation line attaches to. None until this file has produced
        #: one, so nothing leaks across a file or a heading boundary.
        anchor: Found | None = None
        before = len(found)
        for n, line in enumerate(text.splitlines(), 1):
            h = _HEADING.match(line)
            if h:
                heading = h.group(2).strip()
                heading_line = n
                phase_ident = ""
                anchor = None
                continue
            m = _CHECK.match(line)
            if not m:
                # Annotations live on the lines under their item -- and `anchor` is
                # reset per FILE, because `found` is not. A `**Globs:**` line at the top
                # of `b.md` was landing on the last task of `a.md`: a mis-attribution
                # invisible in the plan, since that task's `source` still points at its
                # own line, so the operator sees globs it never declared and no way to
                # tell where they came from.
                g = _GLOBS.search(line)
                if g and anchor is not None:
                    anchor.globs = [x.strip() for x in g.group(1).split(",") if x.strip()]
                nd = _NEEDS.search(line)
                if nd and anchor is not None:
                    anchor.needs = _parse_needs(nd.group(1))
                continue
            done = m.group(2).lower() == "x"
            body = m.group(3).strip()
            ident, body = _split_id(body)
            body = _clean_title(body)
            if heading and not phase_ident:
                # The heading's OWN id wins when it has one: `### 142.A — the
                # scaling-law advisor is wrong` is not a guess, it is the id the
                # project has been writing in commit trailers and in `Needs:` lines
                # for months. Slugging it to `142A-THE-SCALING-LAW-ADV` broke 39 of
                # the 47 declared dependencies in a real repository -- they pointed at
                # `142.A`, which then existed nowhere, and unknown dependencies are
                # treated as unmet, so the work imported permanently blocked.
                declared, rest = _split_id(heading)
                phase_ident = _unique(declared, _slug(heading, 24).upper(), taken)
                found.append(
                    Found(
                        kind="phase",
                        ident=phase_ident,
                        title=_clean_title(rest if declared else heading),
                        source=f"{rel}:{heading_line}",
                        # Whether the id was READ or DERIVED. `_adopt_child_prefix`
                        # must not overrule a read one: `### 142.A` whose tasks are
                        # `142.1`, `142.2` has a child prefix of `142`, and taking it
                        # renamed the phase out from under every `Needs: 142.A` in the
                        # file.
                        extra={"id_from_heading": bool(declared) and declared == phase_ident},
                    )
                )
            anchor = Found(
                kind="task",
                ident=_unique(ident, f"{phase_ident or 'T'}.{_slug(body, 20)}", taken),
                title=body[:120],
                source=f"{rel}:{n}",
                done=done,
                extra={"phase": phase_ident, "id_from_source": bool(ident)},
            )
            found.append(anchor)
        if len(found) == before:
            empty.append(rel)
    _adopt_child_prefix(found, taken)
    return found, empty


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
        if not best or len(voters) < _PREFIX_QUORUM or best in taken:
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
        for title, body, line in sections:
            found.append(
                Found(
                    kind=kind,
                    ident=ident(title, rel),
                    title=title[:120],
                    source=f"{rel}:{line}",
                    body=body[:2000],
                    extra=extra(title, body, rel) if extra else {},
                )
            )
    return found, empty


def scan_lessons(repo: Path) -> tuple[list[Found], list[str]]:
    """`## ` headings in a lessons corpus, each with the prose beneath it.

    Deliberately shallow. A lessons file accumulated over years contains rules that no
    longer apply to symbols that no longer exist, and deciding which is a judgement the
    agent makes with the operator — see the `import-existing-project` prompt. Importing
    all of them and letting `recall` rank them is better than importing none, because a
    lesson nobody can search is a lesson nobody applies.
    """
    return _scan_sections(repo, LESSON_GLOBS, "lesson", lambda t, _r: f"L-{_slug(t, 32)}")


def scan_decisions(repo: Path) -> tuple[list[Found], list[str]]:
    """One ADR file = one decision. The oldest convention in the list and the clearest."""
    found: list[Found] = []
    empty: list[str] = []
    for path in _files(repo, DECISION_GLOBS):
        # `docs/adr/README.md` is the index OF the decisions, not one of them, and it
        # imported as a decision titled "Architecture Decision Records" whose body was
        # a table of contents. Every ADR directory has one.
        if path.stem.lower() in _DECISION_INDEX_STEMS:
            continue
        rel = str(path.relative_to(repo))
        text = path.read_text("utf-8", errors="replace")
        m = re.search(r"^#\s+(.*)$", text, re.M)
        title = (m.group(1) if m else path.stem).strip()
        status = ""
        sm = re.search(r"^##+\s*status\s*$\n+(.+)$", text, re.M | re.I)
        if sm:
            status = sm.group(1).strip().lower()
        dm = re.search(r"^##+\s*decision\s*$\n+(.+?)(?=\n##|\Z)", text, re.M | re.I | re.S)
        found.append(
            Found(
                kind="decision",
                ident=f"D-{_slug(path.stem, 32)}",
                title=title[:140],
                source=rel,
                body=(dm.group(1).strip() if dm else text.strip())[:2000],
                extra={"status": "superseded" if "supersed" in status else "accepted"},
            )
        )
    if not found:
        empty.extend(str(p.relative_to(repo)) for p in _files(repo, DECISION_GLOBS))
    return found, empty


def scan_research(repo: Path) -> tuple[list[Found], list[str]]:
    """`## ` sections of a research log, with their verdict if one is stated.

    A REFUTED entry is worth as much as an adopted one — it is what stops the next
    session re-researching something that was already killed by a probe — so the
    verdict is carried across rather than flattened into prose.
    """
    return _scan_sections(
        repo,
        RESEARCH_GLOBS,
        "research",
        lambda t, _r: f"R-{_slug(t, 32)}",
        lambda _t, body, _r: {"verdict": _verdict(body)},
    )


def _verdict(body: str) -> str:
    m = re.search(r"\b(CONFIRMED|REFUTED|THEORETICAL)\b", body)
    return m.group(1) if m else "THEORETICAL"


def scan_journal(repo: Path) -> tuple[list[Found], list[str]]:
    """Engineering-journal entries: one `##` heading = one thing that happened.

    Imported as session NOTES rather than as tasks or lessons, because that is what a
    journal entry is — the agent's (or the team's) record of work done, which is
    exactly the shape `session note` already has. That also makes them searchable
    through the path `recall` already knows, instead of inventing a seventh source.
    """
    return _scan_sections(
        repo,
        JOURNAL_GLOBS,
        "journal",
        lambda t, rel: f"J-{_slug(rel, 20)}-{_slug(t, 28)}",
        lambda t, body, rel: {"at": _date_hint(t, body[:200], rel)},
    )


def scan_optmem(repo: Path) -> tuple[list[Found], list[str]]:
    """OptMem's `LOG.txt` — one fixed-width record per line, one memory per record.

    This is the source with the highest value per byte and the one a fresh queue most
    obviously destroys: months of "this box has 8 H200s", "use -n 16 not -n auto",
    "that reviewer can exit 0 having degenerated". None of it is derivable from the
    code, none of it is in the journal, and an agent that loses it re-discovers each
    fact the expensive way.

    Imported as session notes, like journal entries, for the same reason: a memory is a
    record of something that was true, not a rule and not a task. Records are NOT
    parsed for structure beyond `#n date text` — the body is deliberately free-form and
    inventing a schema for it would drop the half that did not fit.
    """
    found: list[Found] = []
    empty: list[str] = []
    for path in _files(repo, OPTMEM_GLOBS):
        rel = str(path.relative_to(repo))
        before = len(found)
        for line_no, ln in enumerate(path.read_text("utf-8", errors="replace").splitlines(), 1):
            m = _OPTMEM.match(ln)
            if not m or not m.group(3):
                continue
            num, date, text = m.group(1), m.group(2), m.group(3)
            found.append(
                Found(
                    kind="memory",
                    # Numbered by the store, so a re-import after new memories were
                    # added skips the ones already there instead of duplicating them.
                    ident=f"M-{int(num):04d}",
                    title=text[:120],
                    source=f"{rel}:{line_no}",
                    body=text,
                    # The title is an EXCERPT of the body, not a heading above it: a
                    # memory is one line and has no title. Without saying so the note
                    # writer concatenates the two and every memory arrives with its
                    # first 120 characters printed twice.
                    extra={"at": date, "n": int(num), "title_is_excerpt": True},
                )
            )
        if len(found) == before:
            empty.append(rel)
    return found, empty


def scan_branches(repo: Path) -> list[Found]:
    """Branches with commits not on the default branch — work genuinely in flight.

    The most valuable scan here and the least guessy: git knows, exactly, which branches
    carry unmerged commits. A project adopting Orchard mid-stream usually has two or
    three, and those are the items whose absence from the queue would be most misleading.
    """
    from ..infra import worktree as W

    base = W.default_branch(repo)
    r = P.run(
        ["git", "-C", str(repo), "for-each-ref", "--format=%(refname:short)", "refs/heads/"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if r.returncode != 0:
        return []
    out: list[Found] = []
    for branch in [b.strip() for b in r.stdout.splitlines() if b.strip()]:
        if branch == base:
            continue
        c = P.run(
            ["git", "-C", str(repo), "rev-list", "--count", f"{base}..{branch}"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        ahead = int(c.stdout.strip() or 0) if c.returncode == 0 else 0
        if ahead <= 0:
            continue
        out.append(
            Found(
                kind="branch",
                ident=f"B-{_slug(branch, 32)}",
                title=f"{branch} ({ahead} commit(s) not on {base})",
                source=f"git:{branch}",
                extra={"branch": branch, "ahead": ahead},
            )
        )
    return out


#: How many drifted phases a note names before it says "and more". A note nobody
#: finishes reading is a note nobody acts on.
_NOTE_EXAMPLES = 6


def _flag_shipped_phases_with_open_tasks(plan: ImportPlan) -> None:
    """Report phases whose heading says finished while their checkboxes say otherwise.

    Real drift, measured on a real repository, and the reason it matters is one-sided:
    if the heading is right the queue is about to hand out work that is already done,
    and the agent that picks it up will redo it. Reported, never acted on -- which of
    the two is stale is exactly the judgement the operator has to make, and guessing it
    would either resurrect finished work or silently close live work.
    """
    open_by_phase: dict[str, int] = {}
    for f in plan.found:
        if f.kind == "task" and not f.done and f.extra.get("phase"):
            open_by_phase[f.extra["phase"]] = open_by_phase.get(f.extra["phase"], 0) + 1
    drifted = [
        f
        for f in plan.found
        if f.kind == "phase" and _DONE_MARKER.search(f.title) and open_by_phase.get(f.ident)
    ]
    if not drifted:
        return
    names = ", ".join(
        f"{f.ident} ({open_by_phase[f.ident]} open)" for f in drifted[:_NOTE_EXAMPLES]
    )
    plan.notes.append(
        f"{len(drifted)} phase heading(s) say the work is finished while their "
        f"checkboxes are still unticked: {names}"
        + (", ..." if len(drifted) > _NOTE_EXAMPLES else "")
        + ". Ask the operator which is stale BEFORE anyone claims from them — imported "
        "as-is the queue will offer work that may already be done."
    )


def _flag_unresolvable_needs(plan: ImportPlan, known: set[str]) -> None:
    """Report dependencies pointing at ids nothing will have produced.

    They are not dropped: an unknown dependency is treated as UNMET on purpose, so a
    typo surfaces as blocked work rather than as work that starts early. But it has to
    be SAID -- the failure mode it produces is an item that is simply never offered,
    which looks exactly like an item nobody has got to yet.

    Checked against what is ALREADY in the queue as well as what this scan proposed.
    Against `plan.found` alone -- which by construction excludes every already-imported
    item -- the second run of an incremental import reported every dependency on a
    first-run item as missing, with a consequence sentence that is false: the fold
    resolves dependencies against the folded queue, not against one scan's output. A
    note whose stated consequence is wrong sends the operator to fix something that is
    not broken, and is worse than no note.
    """
    ids = {f.ident for f in plan.found} | known
    missing: dict[str, list[str]] = {}
    for f in plan.found:
        for d in f.needs:
            if d not in ids:
                missing.setdefault(d, []).append(f.ident)
    if not missing:
        return
    named = ", ".join(
        f"{d} (needed by {', '.join(who[:2])})"
        for d, who in sorted(missing.items())[:_NOTE_EXAMPLES]
    )
    plan.notes.append(
        f"{len(missing)} declared dependenc(ies) point at ids this scan did not find: "
        f"{named}" + (", ..." if len(missing) > _NOTE_EXAMPLES else "") + ". They are "
        "imported as-is and treated as UNMET, so the items needing them will not be "
        "offered until the id is created or the dependency corrected."
    )


def plan_import(
    repo: Path, state=None, *, include_done: bool = False, max_tasks: int = 200
) -> ImportPlan:
    """Read everything importable. Writes NOTHING.

    ``state`` is the already-folded queue, if there is one: anything whose id is
    already present is reported as skipped rather than proposed again, which is what
    makes a second run safe after the operator edits the todo file.

    **Completed tasks are excluded by default.** Run against the repository this was
    extracted from, the scan finds 4,799 ticked boxes — a faithful history and useless
    as a queue, because none of it is work anyone will do. What matters for "continue
    from where we left off" is the OPEN items, the branches still in flight, and the
    memory (lessons, decisions). `include_done=True` imports the rest as closed items
    when the history itself is what you want.

    ``max_tasks`` is a guard rail rather than a policy: an import that silently writes
    five thousand events into a log that is committed to git is not recoverable by
    anything short of editing history. Over the cap, the plan reports the overflow and
    refuses to propose it — narrow the scope, or raise the cap deliberately.
    """
    plan = ImportPlan()
    known: set[str] = set()
    if state is not None:
        known |= set(getattr(state, "items", {}))
        known |= set(getattr(state, "lessons", {}))
        known |= set(getattr(state, "decisions", {}))
        known |= set(getattr(state, "research", {}))
        # Journal entries and memories land as session NOTES, not as their own records,
        # so "is this already imported" cannot be answered by an id table. It is
        # answered by the `ident` each note carries -- without this a second import
        # duplicated every journal entry and every memory while reporting success, and
        # the existing idempotency test never saw it because its fixture had neither.
        for sess in getattr(state, "sessions", {}).values():
            known |= {n.get("ident", "") for n in sess.notes if n.get("ident")}
    known.discard("")

    done_skipped = 0
    deferred_done: dict[str, Found] = {}
    proposed: set[str] = set()
    for scan in (
        scan_todos,
        scan_lessons,
        scan_decisions,
        scan_research,
        scan_journal,
        scan_optmem,
    ):
        items, empty = scan(repo)
        for f in items:
            # Uniquify BEFORE the already-imported check, and against what THIS scan
            # proposed rather than against what is in the queue. Both halves matter:
            #
            # Ids are the queue's primary key, and a collision is silent data loss --
            # the second event folds over the first, one item disappears, and the
            # import reports both as written. Two lessons whose titles agree in their
            # first 32 characters collide; a real corpus had 42 such pairs. Doing it
            # once here rather than inside each scanner is six private `taken` sets
            # avoided, and cross-scanner collisions caught.
            #
            # Seeding from `known` instead would make the id depend on what had
            # already been imported: the pair that became `L-x` and `L-x-2` on the
            # first run would come out `L-x-2`, `L-x-3` on the second, and an import
            # advertised as idempotent would duplicate its whole corpus on every run.
            f.ident = _unique("", f.ident, proposed)
            if f.ident in known:
                plan.skipped_existing.append(f.ident)
                continue
            if f.kind == "task" and f.done and not include_done:
                deferred_done[f.ident] = f
                done_skipped += 1
                continue
            plan.found.append(f)
        plan.empty_sources.extend(empty)

    # A finished task that an OPEN task depends on has to come too, as done. Skipping
    # it leaves the open one blocked on an id the queue has never heard of — and an
    # unknown dependency is treated as unmet, deliberately, so the import would land
    # permanently stuck work and look like it had succeeded.
    wanted = {d for f in plan.found for d in f.needs} & set(deferred_done)
    for ident in sorted(wanted):
        plan.found.append(deferred_done[ident])
        done_skipped -= 1
    if wanted:
        plan.notes.append(
            f"{len(wanted)} completed task(s) were imported anyway because open work "
            f"depends on them: {', '.join(sorted(wanted))}. Without them those "
            f"dependencies would be unresolvable and the open items would import "
            f"permanently blocked."
        )
    if done_skipped:
        plan.notes.append(
            f"{done_skipped} already-ticked task(s) were NOT imported. They are history, "
            f"not a queue — pass include_done to bring them in as completed items."
        )

    tasks = [f for f in plan.found if f.kind == "task"]
    if len(tasks) > max_tasks:
        plan.found = [f for f in plan.found if f.kind not in ("task", "phase")]
        plan.notes.append(
            f"REFUSING to propose {len(tasks)} tasks (cap {max_tasks}). That many is "
            f"almost certainly a whole project history rather than a queue, and an "
            f"import writes events into a log that is committed. Narrow it — point the "
            f"import at one todo file, or raise the cap deliberately once you have "
            f"looked at what it would write. The phases are withheld with them: a "
            f"queue of empty phases is not a smaller import, it is a misleading one."
        )
    else:
        # A phase whose tasks were all filtered out -- finished, or already in the
        # queue -- is an empty container. Importing 790 of them alongside zero tasks
        # reads as "this project has 790 phases of work", which is the opposite of
        # true. Dropped here rather than at apply time so the PROPOSAL is what gets
        # written, and the operator reviews the thing that will happen.
        _flag_shipped_phases_with_open_tasks(plan)
        _flag_unresolvable_needs(plan, known)
        live_phases = {f.extra.get("phase") for f in plan.found if f.kind == "task"}
        # ...and any phase an open item declares a dependency ON. A finished phase that
        # open work waits for is not an empty container: drop it and the dependency
        # becomes unknown, which is treated as unmet, so the open item imports
        # permanently blocked while the import reports success. Same rule as the
        # finished-TASK carve-out above, and it was missing here.
        live_phases |= {d for f in plan.found for d in f.needs}
        empty_phases = [f for f in plan.found if f.kind == "phase" and f.ident not in live_phases]
        if empty_phases:
            plan.found = [f for f in plan.found if f not in empty_phases]
            plan.notes.append(
                f"{len(empty_phases)} phase(s) had no open task left and were not "
                f"imported. They are finished or already in the queue; an empty phase "
                f"is a container, not work."
            )
    for f in scan_branches(repo):
        f.ident = _unique("", f.ident, proposed)
        if f.ident not in known:
            plan.found.append(f)

    if not plan.found and not plan.skipped_existing:
        plan.notes.append(
            "Nothing recognisable was found. That is not necessarily wrong — this looks "
            "for todo checklists, a lessons corpus, ADR files and unmerged branches in "
            "their usual locations. If this project keeps them somewhere else, tell the "
            "agent where and it can file them with `orchard task add` / `lesson add` / "
            "`decision add` directly."
        )
    return plan


def apply_import(repo: Path, log: EventLog, plan: ImportPlan) -> dict[str, int]:
    """Write the proposal to the log. Called only after someone has looked at it.

    Tasks marked done are recorded as done **with their source**, not silently: the
    evidence is "a human ticked this box in docs/todo.md:41", which is exactly what it
    is, and a reader can go and check. Orchard does not invent completion.
    """
    counts: dict[str, int] = {}

    def bump(kind: str) -> None:
        counts[kind] = counts.get(kind, 0) + 1

    for f in plan.by_kind("phase"):
        log.append("phase.added", f.ident, {"title": f.title, "body": f"Imported from {f.source}."})
        bump("phase")
    for f in plan.by_kind("task"):
        log.append(
            "task.added",
            f.ident,
            {
                "parent": f.extra.get("phase", ""),
                "title": f.title,
                "needs": f.needs,
                "globs": f.globs,
                "body": f"Imported from {f.source}.",
            },
        )
        bump("task")
        if f.done:
            log.append(
                "item.completed",
                f.ident,
                {"kind": "task", "imported": True, "evidence": f"ticked in {f.source}"},
            )
            bump("task_done")
    for f in plan.by_kind("lesson"):
        log.append(
            "lesson.recorded",
            f.ident,
            {"title": f.title, "rule": f.body, "tags": ["imported"], "seen_in": [f.source]},
        )
        bump("lesson")
    for f in plan.by_kind("decision"):
        log.append(
            "decision.recorded",
            f.ident,
            {
                "title": f.title,
                "decision": f.body,
                "status": f.extra.get("status", "accepted"),
                "context": f"Imported from {f.source}.",
            },
        )
        bump("decision")
    # Journal entries and OptMem records are both "a record of something that was
    # true", which is what a session note IS. One session each rather than one per
    # entry: N synthetic sessions would bury the real ones in `replay`.
    for kind, sid, what in (
        ("journal", "s-imported-journal", "journal entr(ies)"),
        ("memory", "s-imported-memory", "cross-session memor(ies)"),
    ):
        entries = plan.by_kind(kind)
        if not entries:
            continue
        log.append("session.started", sid, {"model": "(imported)", "tool": "orchard import"})
        for n, f in enumerate(entries):
            log.append(
                "session.note",
                sid,
                {
                    "seq": n,
                    "ident": f.ident,
                    "text": (
                        f.body
                        if f.extra.get("title_is_excerpt")
                        else f"{f.title}\n\n{f.body}".strip()
                    )[:4000],
                    "at": f.extra.get("at", ""),
                    "source": f.source,
                },
            )
            bump(kind)
        log.append(
            "session.ended",
            sid,
            {"summary": f"Imported {len(entries)} {what} from this project."},
        )
    for f in plan.by_kind("research"):
        log.append(
            "research.recorded",
            f.ident,
            {
                "question": f.title,
                "claim": f.body[:600],
                "verdict": f.extra.get("verdict", "THEORETICAL"),
                "sources": [f.source],
            },
        )
        bump("research")
    for f in plan.by_kind("branch"):
        log.append(
            "task.added",
            f.ident,
            {
                "title": f.title,
                "body": (
                    f"Imported from {f.source}: this branch carries "
                    f"{f.extra.get('ahead', '?')} commit(s) not on the base branch. "
                    f"Declare its globs before anyone claims it."
                ),
            },
        )
        bump("branch")
    return counts
