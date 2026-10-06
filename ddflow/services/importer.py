"""Import an existing project's history so ddflow can continue it, not restart it.

A project that adopts ddflow on day 400 has four hundred days of work already: a todo
file with things done and things not, a lessons corpus, architecture decisions, bugs
fixed, branches half-landed. `ddflow adopt` used to start the queue **empty**, which
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

import fnmatch
import os
import re
import stat
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Config
from ..core.model import ABANDONED, DONE, OPEN
from ..core.schedule import is_external
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
#: The distilled companion some projects keep beside the lessons corpus. Hand-written ones
#: carry information the corpus does not -- which rules the team considers essential, in
#: what words -- and a GENERATED one carries nothing the corpus lacks, so it is skipped.
LESSON_SUMMARY_GLOBS = (
    "docs/lessons-summary.md",
    "docs/lessons_summary.md",
    "docs/LESSONS-SUMMARY.md",
    "LESSONS-SUMMARY.md",
)
#: A heading that says the work is finished, in the spellings projects actually use.
#: Checked against the PHASE heading only: a phase marked shipped whose tasks are still
#: unticked is the single most valuable thing this scan can tell the operator, because
#: it is drift they cannot see and the queue would otherwise hand that work out again.
#:
#: A STATUS, not the word: `beyond the shipped two`, `Definition of done`, `(closed beta)`
#: and `NOT shipped in 137.E` are prose, and matched case-insensitively anywhere they
#: raised a permanent "finished with an open task" alarm on every import of
#: home-simulator (15 such headings across it and run_nemo_run, bug B-imp-shipped-prose).
#: What counts: the whole word in capitals or a check mark; or a lowercase one that ENDS
#: its clause (`(shipped)`, `— done`, `closed 2026-08-07`, `— shipped in 0.3`) where it
#: opens the heading, an aside clause or the text after a separator, or precedes a
#: separator. Titles reach this with their bold already stripped (`_clean_title`), so
#: bold is not a signal here. Ask `_claims_done`, which also handles negation.
_DONE_WORD = r"(?:shipped|closed|done|complete[d]?)"
#: The whole word: not `closed-loop`, not `shipped-vs-ticked`.
_WORD_END = r"(?![\w-])"
#: What may follow a lowercase status word for it to end its clause: the end, a closing
#: bracket, a separator, sentence punctuation, a date, or a version (`shipped in 0.3`).
_CLAUSE_END = (
    r"(?=\s*(?:$|[)\],;(\u2014\u2013:|]|[.!?](?:\s|$|[)\]])|\s-+\s|\d{4}-\d{2}"
    r"|(?i:in\s+(?:v\d|\d+\.\d)|now\b)))"
)
#: The word in capitals, in a heading that is not itself written in capitals.
#: Never as the object of `OF` or `TO BE` (`Definition of DONE`, `WORK TO BE DONE`).
_NOT_AFTER_OF = r"(?<!\bOF )(?<!\bof )(?<!\bBE )(?<!\bbe )"
_DONE_CAPS = re.compile(rf"{_NOT_AFTER_OF}\b(?:SHIPPED|CLOSED|DONE|COMPLETED?){_WORD_END}")
#: In a heading written in capitals every word is a capital word, so there the status
#: word must END its clause (`PHASE 12 SHIPPED`), and not as the object of `OF` or `TO BE`
#: (`DEFINITION OF DONE`, `WORK TO BE DONE`).
_DONE_CAPS_ENDING = re.compile(
    rf"{_NOT_AFTER_OF}\b(?:SHIPPED|CLOSED|DONE|COMPLETED?){_WORD_END}{_CLAUSE_END}"
)
_DONE_CHECK = re.compile(r"[\u2705\u2714]")
#: A lowercase word that opens the heading, an aside clause or the text after a separator,
#: and ends its clause.
_DONE_CLAUSE = re.compile(
    rf"(?i:(?:^\W*|[(\[,;]\s*(?:now\s+)?|(?:[\u2014\u2013:|]|\s-+\s)\s*)"
    rf"(?P<w>{_DONE_WORD}){_WORD_END}{_CLAUSE_END})"
)
#: The one negation prefix both checks use: `NOT`, `NOT YET`, `NEVER`.
_NEGATION = r"\b(?:NOT|NEVER)\s+(?:YET\s+)?"
#: A negation that ends right before a status word: `NOT SHIPPED`, `not yet done`.
_NEGATED_BEFORE = re.compile(rf"{_NEGATION}\W*$", re.I)
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
#: nothing in ddflow could read it, so `recall` could answer "what did we decide" and
#: not "what happened in March".
JOURNAL_GLOBS = (
    "docs/log/*.md",
    "docs/journal/*.md",
    "docs/CHANGELOG.md",
    "CHANGELOG.md",
    "docs/log.md",
    # Upper-case too: a case-SENSITIVE filesystem treats it as a different file, and one
    # real project keeps its whole 287-entry journal there, which the lower-case pattern
    # silently never read. Where it is instead a generated index of monthly shards, its
    # only section is "Index", which `_is_index_section` drops.
    "docs/LOG.md",
    "JOURNAL.md",
)

#: Every file pattern the scanners read, in one tuple. `_instruction_vars` counts these
#: to decide whether to OFFER the import at handshake time, and it counted three of the
#: seven families — so a project whose entire history is an engineering journal and a
#: memory store was told nothing, which is precisely the case the offer exists for.
SOURCE_GLOBS = (
    TODO_GLOBS
    + LESSON_GLOBS
    + LESSON_SUMMARY_GLOBS
    + DECISION_GLOBS
    + RESEARCH_GLOBS
    + JOURNAL_GLOBS
    + OPTMEM_GLOBS
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
#:   TITLED    — `**38.9 Free-text arg vocabulary.** detail`: the bold wraps a DOTTED id
#:               and the title with no separator between them (home-simulator writes
#:               this). Dotted only -- `**L2 cache misses**` is prose -- and the dot is
#:               checked in `_split_id`, since `_ID` also matches `L2`. Prose that
#:               opens with a dotted number (`**3.5 million users**`) is read as an id
#:               too: the shape cannot tell them apart.
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
    rf"\[({_ID})\]|"
    # ANNOTATED: `**MC-F5 (THEORETICAL — no code change)**` -- the bold wraps the id and
    # an aside about its status, and none of the other shapes saw an id there, so the
    # item imported under a prose slug that no `Needs:` line and no commit trailer names.
    rf"\*\*({_ID})\s*\([^)]{{0,80}}\)\*\*"
    r")\s*[—\-:]?\s*"
    rf"|^(?:\*\*)?({_ID})(?:\s*\([^)]{{0,30}}\))?\s*[—:]\s+"
    rf"|^\*\*({_ID})\s+(?=[^\s*])"
)


def _is_id(token: str) -> bool:
    """An id carries a digit, and is either dotted or starts with a letter.

    The digit is what separates `P2.T1` from a title beginning with a word. The second
    clause is what lets `160.A` in -- dotted, so the structure itself says "id" -- while
    keeping `3` and `2026` out of a list that starts with a number.
    """
    return bool(token) and any(c.isdigit() for c in token) and ("." in token or token[0].isalpha())


def _titled(text: str) -> bool:
    """Whether `text` carries its id in the TITLED shape (`**38.9 Free-text ...**`) --
    by the same rule `_split_id` applies, so `**L2 cache misses**` is prose to both."""
    m = _ITEM_ID.match(text)
    return bool(m and m.group(5) and _split_id(text)[0])


def _split_id(text: str) -> tuple[str, str]:
    """`(ident, remaining text)`. `("", text)` when the line carries no id."""
    m = _ITEM_ID.match(text)
    if not m:
        return "", text
    token = m.group(1) or m.group(2) or m.group(3) or m.group(4) or ""
    if not token and m.group(5):
        token = m.group(5)
        if "." not in token.strip(".") or not _is_id(token):
            return "", text
        # The bold still wraps the title: keep its opening `**`, so the title reads
        # `Free-text arg vocabulary.` rather than ending in a stray `**`.
        return token, "**" + text[m.end() :]
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
#: `**Resources:** gpu:4, vllm-fleet` -- what the work runs on (see `schedule.resources`).
_RESOURCES = re.compile(r"\*\*Resources?:?\*\*:?\s*(.+)", re.I)
#: A markdown heading, used to group checkboxes into phases.
_HEADING = re.compile(r"^(#{1,4})\s+(.*)$")

#: DISPOSITIONS: an open `- [ ]` box that is NOT work to start. Two kinds, because they
#: need different treatment in the queue:
#:
#:   CLOSED -- the operator decided it will not be done (declined, refuted, superseded).
#:             History, exactly like a ticked box: not imported by default, imported as
#:             ABANDONED with `include_done`.
#:   HOLD   -- it may become work later (deferred, theoretical, blocked). Imported as
#:             BLOCKED with the reason, so it is visible, never offered, and one
#:             `ddflow unblock` away from being work.
#:
#: The vocabulary and the positions it is read in are taken from the picker of the
#: project this was extracted from (`scripts/phase.py::_NOT_WORK`), where each word was
#: MEASURED over ~1,100 open boxes. Without them the import offered every one of that
#: repository's deferred and refuted findings as ready work: 1,179 open tasks, where its
#: own picker offers ~120. A queue that hands out operator-declined work is worse than an
#: empty one, because the agent that picks it up does it.
_CLOSED_MARKERS = (
    "DECLINED",
    "REFUTED",
    "REFUSED",
    "RETRACT",
    "SKIPPED",
    "SUPERSEDED",
    "WONTFIX",
    "WON'T FIX",
    "OUT OF SCOPE",
    "OPTED OUT",
    "OPT-OUT",
    "DESCOPED",
    "OBSOLETE",
)
_HOLD_MARKERS = (
    "DEFERRED",
    "THEORETICAL",
    "BLOCKED",
    "PRE-EXISTING",
    "NOT FIXED",
    "NOT WORKED",
    "ON HOLD",
    "PARKED",
)
#: Inside a title's parenthesised aside `PRE-EXISTING` records WHEN a bug originated --
#: `**MC-F1 (roborev 72 -- pre-existing, HIGH)**` is fully specified work -- so it does
#: not dispose there. After the title it does: nothing else would be written there.
_ASIDE_NOT_DISPOSITIONS = ("PRE-EXISTING",)
#: How far past the title an annotation is read. The measured window; a whole item body
#: reaches prose that merely DESCRIBES a deferral.
_ANNOTATION_CHARS = 160
#: A heading that disposes everything under it: `### Deferred`, `## DECLINED items`.
_SECTION_HOLD = ("DEFERRED", "ON HOLD", "PARKED")
#: `### P42.8 — Future work (deliberately not in this phase)`: a heading whose title IS
#: "future work" holds; `## Phase 5 — Future work planning` is a phase about it.
_FUTURE_WORK = re.compile(r"^future work\b\s*(?:\(|[\u2014\u2013:]|\s-+\s|\.(?:\s|$)|$)", re.I)
#: A heading that says its own section is LIVE: `### Phase 39 follow-ups (not started)`
#: filed under a `## Phase 38` whose STATUS is SHIPPED. Inheriting the ancestor's verdict
#: dropped both of its open items as history (home-simulator, 2026-09-29).
_SECTION_LIVE = ("NOT STARTED", "NOT YET STARTED", "IN PROGRESS", "REOPENED", "RE-OPENED")
_SECTION_CLOSED = ("DECLINED", "OPTED OUT", "DESCOPED")
#: A section's own `**STATUS**:` line. Its VERDICT is the leading token, never the whole
#: line: `IN PROGRESS -- 373 of 534 (B.5 CLOSED 2026-08-17)` is live, and matching the
#: whole line read it as closed.
_STATUS_LINE = re.compile(r"^\s*\*\*STATUS\*\*\s*:?\s*(.+)$", re.I)
_STATUS_CLOSED = ("SHIPPED", "CLOSED", "DECLINED", "SUPERSEDED", "ABANDONED", "DONE", "COMPLETE")
_STATUS_HOLD = ("DEFERRED", "WATCH", "ON HOLD", "PARKED", "BLOCKED")
#: Words that contradict a closed verdict later on the same STATUS line.
_STATUS_LIVE = ("IN PROGRESS", "REOPENED", "RE-OPENED")
_TITLE_ASIDE = re.compile(r"\(([^)]*)\)")
#: Where one clause of an aside ends: `(MED, deferred from P21.4)` is two clauses.
_ASIDE_CLAUSE = re.compile(r"[,;:\u2014\u2013]|\s-{1,2}\s")


#: Words that may stand before a verdict without making it prose: `(marked deferred)`,
#: `(now on hold)`, `(operator: deferred)`.
_VERDICT_FILLER = re.compile(
    r"^(?:(?:NOW|WAS|IS|BEEN|BEING|MARKED|EXPLICITLY|OPERATOR|ALSO|STATUS|THE)\W+)*"
)


def _leads_with(clause: str, markers: tuple[str, ...]) -> bool:
    """Whether `clause` BEGINS with a marker -- the whole word, so `watchdog`,
    `parkedcar` and `deferred_from` do not -- past emphasis, emoji and filler words in front of it."""
    head = re.sub(r"^[^A-Za-z0-9]+", "", clause).upper()
    head = _VERDICT_FILLER.sub("", head)
    return any(re.match(rf"{re.escape(m)}(?!\w)", head) for m in markers)


def _verdict_asides(text: str, markers: tuple[str, ...]) -> str:
    """`text` without the parenthesised asides that DESCRIBE rather than dispose.

    An aside is a verdict when one of its clauses LEADS with a marker: `(DEFERRED by the
    operator)`, `(MED, deferred from P21.4)`. `(inventory → todo → deferred notify)` is
    what the item builds, and read as a deferral it held back the very item the
    project's handoff said to start with (home-simulator C11, 2026-09-29).
    """

    def keep(m: re.Match) -> str:
        clauses = _ASIDE_CLAUSE.split(m.group(1))
        leads = any(_leads_with(c, markers) for c in clauses)
        return m.group(0) if leads else " "

    return _TITLE_ASIDE.sub(keep, text)


def _marker_in(text: str, markers: tuple[str, ...]) -> str:
    """The first marker present in `text` at a word START, case-insensitively.

    Word-start rather than substring: `UNBLOCKED` is not `BLOCKED`. The end is left open
    so `RETRACT` still matches `RETRACTED`.
    """
    up = text.upper()
    for m in markers:
        if re.search(rf"(?<![A-Z0-9]){re.escape(m)}", up):
            return m
    return ""


#: A parenthesised aside still open at the end of the line.
_OPEN_ASIDE = re.compile(r"\(([^()]*)$")
#: Where the title of a checkbox with NO bold run ends: the first dash separator.
_NONBOLD_SPLIT = re.compile(r"\s+(?:\u2014|--|-)\s+")
#: How far into such a title a `MARKER:` lead may sit.
_LEAD_WORD_CHARS = 25


def _split_nonbold(body: str) -> tuple[str, str]:
    """(title, annotation) of a checkbox with no bold run.

    The title is the FIRST SENTENCE before any dash separator; everything after -- the
    separator's tail, and later sentences ("Kubernetes backend. Out of scope for v1.")
    -- is annotation, because that is where a disposition is written about the item
    rather than in it.
    """
    m = _NONBOLD_SPLIT.search(body)
    head, tail = (body[: m.start()], body[m.end() :]) if m else (body, "")
    first, _sep, rest = head.partition(". ")
    return first, f"{rest} {tail}".strip()


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
        return "hold", "in an archive file (release its phase to drive it)"
    return "", ""


def _ids_config(repo: Path):
    """The project's config for the id templates an import mints with; the shipped
    defaults when it cannot be read (a proposal must not fail for it)."""
    from ..config import Config

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
    from ..core import ids as IDS

    ids_cfg = _ids_config(repo)
    found: list[Found] = []
    empty: list[str] = []
    taken: set[str] = set()
    for path in _files(repo, globs):
        rel = str(path.relative_to(repo))
        text = path.read_text("utf-8", errors="replace")
        # An ARCHIVE file's open boxes are history until someone names the section --
        # the source project's rule for its 20,000-line `docs/todo.md`, where no
        # classifier over the prose is trustworthy because "is this work?" is a property
        # of what the operator intends, not of the text. Held, not dropped: still
        # searchable, still resolvable as a dependency, and `ddflow unblock <phase>`
        # releases a whole section.
        archived = any(fnmatch.fnmatch(rel, g) for g in archive)
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
                    **{"user-text": _slug(heading, 24).upper()},
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
                    },
                )
                found.append(phase_found)
            derived = IDS.render(
                ids_cfg,
                "imported_task",
                check=False,
                phase=phase_ident or "T",
                slug=_slug(unsplit or body, 20),
            )
            chosen = _unique(ident, derived, taken)
            anchor = Found(
                kind="task",
                ident=chosen,
                title=body[:120],
                source=f"{rel}:{n}",
                done=done,
                extra={
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
                    title=title[:120],
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


def scan_lessons(
    repo: Path, globs: tuple[str, ...] = LESSON_GLOBS
) -> tuple[list[Found], list[str]]:
    """Every lesson in a lessons corpus, each with the prose beneath it.

    Deliberately shallow about MEANING. A lessons file accumulated over years contains
    rules that no longer apply to symbols that no longer exist, and deciding which is a
    judgement the agent makes with the operator — see the `import-existing-project`
    prompt. Importing all of them and letting `recall` rank them is better than importing
    none, because a lesson nobody can search is a lesson nobody applies.

    Careful about STRUCTURE, because two layouts are common and they differ in the level
    lessons live at (`_lesson_level`). Three things are read out of the body rather than
    left in prose, because each is what a consumer acts on:

    * the project's own lesson id (`L100`) when the heading carries one;
    * a `**Compressed:**` paragraph -- the lesson's one-paragraph form, which is what a
      lessons SUMMARY is made of;
    * `**Seen in:**` pointers, which are provenance.
    """
    found: list[Found] = []
    empty: list[str] = []
    for path in _files(repo, globs):
        rel = str(path.relative_to(repo))
        text = path.read_text("utf-8", errors="replace")
        level = _lesson_level(text)
        raw = _id_entries(text) if level else _sections(text)
        sections = [s for s in raw if not _is_index_section(s[0])]
        if not sections:
            empty.append(rel)
            continue
        for title, body, line in sections:
            m = _LESSON_HEAD.match(title) if level else None
            ident = m.group(1) if m else f"L-{_slug(title, 32)}"
            # The id this lesson was imported under before hyphenated ids were read as
            # ids, so a re-run recognises it instead of importing it a second time.
            legacy = f"L-{_slug(title, 32)}" if m and "-" in ident else ""
            shown = m.group(2).strip() if m and m.group(2).strip() else title
            seen = _one_paragraph(_SEEN_IN.search(body))
            found.append(
                Found(
                    kind="lesson",
                    ident=ident,
                    title=shown[:120],
                    source=f"{rel}:{line}",
                    body=body[:_LESSON_BODY_CHARS],
                    extra={
                        "summary": _one_paragraph(_COMPRESSED.search(body)),
                        "seen_in": [seen[:300]] if seen else [],
                        "tags": [],
                        **({"legacy_ident": legacy} if legacy else {}),
                    },
                )
            )
    return found, empty


#: `- **bold lead.** explanation [L170]` -- a hand-written summary bullet and what it cites.
_BULLET = re.compile(r"^[-*]\s+(.*)$")
_CITES = re.compile(rf"\b{_LESSON_ID}\b")
_TRAILING_CITE = re.compile(r"\s*\[([^\]]*)\]\s*$")
#: How a generated file announces itself, in the first lines.
#: How a generated file announces itself: a comment banner, or the literal DO NOT EDIT.
#: Not the bare word anywhere -- a hand-written bullet saying "never hand-edit generated
#: code" discarded the whole summary it was in (roborev 824).
_GENERATED_MARK = re.compile(r"<!--[^>]*\bGENERATED\b|\bDO NOT EDIT\b")


def _summary_bullet(lines: list[str], source: str, category: str) -> Found:
    """One summary bullet, its lead and the lessons it cites."""
    text = " ".join(" ".join(lines).split())
    lead = re.match(r"\*\*(.+?)\*\*", text)
    tail = _TRAILING_CITE.search(text)
    cites = _CITES.findall(tail.group(1)) if tail else _CITES.findall(text)
    return Found(
        kind="summary",
        ident="",
        title=(lead.group(1) if lead else text)[:120].rstrip(". "),
        source=source,
        body=text,
        extra={"category": category, "cites": list(dict.fromkeys(cites))},
    )


def scan_lesson_summaries(
    repo: Path, globs: tuple[str, ...] = LESSON_SUMMARY_GLOBS
) -> tuple[list[Found], list[str]]:
    """Bullets of a hand-written lessons summary, as `summary` records.

    NOT lessons yet: `_attach_summaries` decides. A bullet citing exactly one lesson the
    import also found becomes that lesson's summary; any other bullet is a consolidated
    rule in its own right and becomes a lesson. A GENERATED summary yields one
    `summary_generated` marker and nothing else -- its every word is already in the
    corpus, and importing it would put each rule in the queue twice.
    """
    found: list[Found] = []
    empty: list[str] = []
    for path in _files(repo, globs):
        rel = str(path.relative_to(repo))
        text = path.read_text("utf-8", errors="replace")
        if _GENERATED_MARK.search("\n".join(text.splitlines()[:15])):
            found.append(Found(kind="summary_generated", ident="", title="", source=rel))
            continue
        before = len(found)
        category = ""
        current: list[str] = []
        start = 0

        lines = text.splitlines()
        fenced = _fenced(lines)
        for n, ln in enumerate(lines, 1):
            if fenced[n - 1]:
                continue
            h = _SECTION.match(ln)
            b = _BULLET.match(ln)
            if h or b or not ln.strip():
                if current:
                    found.append(_summary_bullet(current, f"{rel}:{start}", category))
                current = []
            if h:
                category = h.group(2).strip()
            elif b:
                current, start = [b.group(1)], n
            elif ln.strip() and current and ln[:1].isspace():
                current.append(ln.strip())
        if current:
            found.append(_summary_bullet(current, f"{rel}:{start}", category))
        if len(found) == before:
            empty.append(rel)
    return found, empty


#: The bold lead and at most ONE separator after it. A greedy separator run ate the
#: dashes of an explanation starting with a flag -- "**Pass** -f to force" became
#: "f to force" (roborev 831).
_LEAD = re.compile(r"^\*\*(.+?)\*\*\s*(?:[.:\u2014\u2013]\s+|-\s+)?")


def _summary_text(bullet: str, title: str = "") -> str:
    """A bullet as a SUMMARY: without its trailing citation, and without its bold lead
    when that lead IS the title the view prints -- keeping it rendered every entry as
    its title twice. A lead that says something the title does not is kept: attached to
    a lesson whose title came from the corpus, dropping it lost the only copy of that
    wording (roborev 831)."""
    text = _TRAILING_CITE.sub("", bullet)
    lead = _LEAD.match(text)
    if not lead:
        return text
    rest = text[lead.end() :].strip()
    a, b = _norm(lead.group(1)), _norm(title)
    same = not title or a.startswith(b) or b.startswith(a)
    return (rest or text) if same else text


def _norm(s: str) -> str:
    return re.sub(r"\W+", " ", s).strip().lower()


def _id_key(ident: str) -> str:
    """`L-12` and `L12` are one lesson: a summary may cite either spelling."""
    return ident.replace("-", "")


def _attach_summaries(scanned: list[Found], plan: ImportPlan) -> None:
    """Resolve `summary` records against the lessons found beside them. In place.

    A bullet that cites exactly one lesson the import found, which has no summary of its
    own, becomes that lesson's summary: that is what the bullet IS, and filing it beside
    the lesson would put one rule in the queue twice. Every other bullet -- citing several
    lessons, or none -- is a consolidated rule nothing else holds, and becomes a lesson
    tagged `summary` with its citations as provenance. No bullet is dropped.
    """
    # Only an id carried by exactly ONE lesson can be cited unambiguously. Two files
    # both holding `L1` (a current corpus and a retrospective) made the dict keep
    # whichever was scanned LAST, and the bullet meant for the current lesson landed on
    # an unrelated old one that the uniquifier later renamed `L1-2` (rubber-duck).
    by_id: dict[str, list[Found]] = {}
    by_key: dict[str, list[Found]] = {}
    for f in scanned:
        if f.kind == "lesson":
            by_id.setdefault(f.ident, []).append(f)
            by_key.setdefault(_id_key(f.ident), []).append(f)
    # The spelling cited first (`L12` names the lesson headed `L12`), and only then the
    # spelling-blind key (`L-12` also names it) -- so a corpus holding BOTH an `L12` and an
    # `L-12` still resolves each exact citation instead of finding the key ambiguous.
    lessons = {i: fs[0] for i, fs in by_id.items() if len(fs) == 1}
    keyed = {k: fs[0] for k, fs in by_key.items() if len(fs) == 1}
    attached = consolidated = 0
    generated: list[str] = []
    out: list[Found] = []
    for f in scanned:
        if f.kind == "summary_generated":
            generated.append(f.source)
            continue
        if f.kind != "summary":
            out.append(f)
            continue
        cites = f.extra.get("cites", [])
        target = (
            (lessons.get(cites[0]) or keyed.get(_id_key(cites[0]))) if len(cites) == 1 else None
        )
        if target is not None and not target.extra.get("summary"):
            target.extra["summary"] = _summary_text(f.body, target.title)
            attached += 1
            continue
        category = f.extra.get("category", "")
        out.append(
            Found(
                kind="lesson",
                ident=f"LS-{_slug(category, 12)}-{_slug(f.title, 28)}",
                title=f.title,
                source=f.source,
                body=f.body,
                extra={
                    "summary": _summary_text(f.body),
                    "seen_in": cites,
                    "tags": ["summary", *([_slug(category, 24)] if category else [])],
                },
            )
        )
        consolidated += 1
    scanned[:] = out
    if attached or consolidated:
        plan.notes.append(
            f"Lessons summary: {attached} bullet(s) became the summary of the one lesson "
            f"they cite; {consolidated} cite several lessons or none and were imported as "
            f"lessons of their own, tagged `summary`. `ddflow render` writes them back out "
            f"as docs/ddflow/LESSONS-SUMMARY.md."
        )
    if generated:
        plan.notes.append(
            f"{', '.join(generated)} is GENERATED from the lessons corpus, so it was not "
            f"imported: each lesson's own `**Compressed:**` paragraph is its summary."
        )


def scan_decisions(
    repo: Path, globs: tuple[str, ...] = DECISION_GLOBS
) -> tuple[list[Found], list[str]]:
    """One ADR file = one decision. The oldest convention in the list and the clearest."""
    found: list[Found] = []
    empty: list[str] = []
    for path in _files(repo, globs):
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
        empty.extend(str(p.relative_to(repo)) for p in _files(repo, globs))
    return found, empty


def scan_research(
    repo: Path, globs: tuple[str, ...] = RESEARCH_GLOBS
) -> tuple[list[Found], list[str]]:
    """`## ` sections of a research log, with their verdict if one is stated.

    A REFUTED entry is worth as much as an adopted one — it is what stops the next
    session re-researching something that was already killed by a probe — so the
    verdict is carried across rather than flattened into prose.
    """
    return _scan_sections(
        repo,
        globs,
        "research",
        _research_ident,
        lambda t, body, _r: {"verdict": _verdict(body), **_research_legacy(t)},
    )


#: A research entry's own id, `R12` or `R-12`, kept like a lesson's (B-import-hyphen-ids).
#: Unhyphenated, at most five digits: ddflow's own research ids are `R` + ten hex
#: characters. The id must END there (`R2-D2 — ...` and `R-12-3 — ...` are titles, not ids).
_RESEARCH_HEAD = re.compile(r"^(R-\d+[a-z]?|R\d{1,5}[a-z]?)(?![-\w])")


def _research_ident(title: str, _rel: str = "") -> str:
    m = _RESEARCH_HEAD.match(title.strip())
    return m.group(1) if m else f"R-{_slug(title, 32)}"


def _research_legacy(title: str) -> dict[str, str]:
    """The slug an id-bearing entry was imported under before its id was kept."""
    return {"legacy_ident": f"R-{_slug(title, 32)}"} if _RESEARCH_HEAD.match(title.strip()) else {}


def _verdict(body: str) -> str:
    m = re.search(r"\b(CONFIRMED|REFUTED|THEORETICAL)\b", body)
    return m.group(1) if m else "THEORETICAL"


def scan_journal(
    repo: Path, globs: tuple[str, ...] = JOURNAL_GLOBS
) -> tuple[list[Found], list[str]]:
    """Engineering-journal entries: one `##` heading = one thing that happened.

    Imported as session NOTES rather than as tasks or lessons, because that is what a
    journal entry is — the agent's (or the team's) record of work done, which is
    exactly the shape `session note` already has. That also makes them searchable
    through the path `recall` already knows, instead of inventing a seventh source.
    """
    return _scan_sections(
        repo,
        globs,
        "journal",
        lambda t, rel: f"J-{_slug(rel, 20)}-{_slug(t, 28)}",
        lambda t, body, rel: {"at": _date_hint(t, body[:200], rel)},
    )


def scan_optmem(repo: Path, globs: tuple[str, ...] = OPTMEM_GLOBS) -> tuple[list[Found], list[str]]:
    """OptMem's `LOG.txt` — one fixed-width record per line, one memory per record.

    This is the source with the highest value per byte and the one a fresh queue most
    obviously destroys: months of "this box has 8 H200s", "use -n 16 not -n auto",
    "that reviewer can exit 0 having degenerated". None of it is derivable from the
    code, none of it is in the journal, and an agent that loses it re-discovers each
    fact the expensive way.

    Imported as operational MEMORIES (`memory.recorded`, see `apply_import`) under the
    store's own id (`M-0041`) and dated when each became true -- the thing `brief` shows
    first and `recall` searches. Not a rule and not a task. Records are NOT
    parsed for structure beyond `#n date text` — the body is deliberately free-form and
    inventing a schema for it would drop the half that did not fit.
    """
    found: list[Found] = []
    empty: list[str] = []
    for path in _files(repo, globs):
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
    carry unmerged commits. A project adopting ddflow mid-stream usually has two or
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
        if (
            f.kind == "task"
            and not f.done
            and not f.extra.get("disposition")
            and f.extra.get("phase")
        ):
            open_by_phase[f.extra["phase"]] = open_by_phase.get(f.extra["phase"], 0) + 1
    drifted = [
        f
        for f in plan.found
        if f.kind == "phase" and _claims_done(f.title) and open_by_phase.get(f.ident)
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
            # A sibling-repo dependency is resolved by `external sync`, not by this
            # scan; calling it unresolvable sent the operator to "correct" a correct
            # dependency (roborev 835).
            if d not in ids and not is_external(d):
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


def _known_ids(state) -> set[str]:
    """Every id already in the queue, so a re-run proposes only what is new."""
    known: set[str] = set()
    if state is not None:
        known |= set(getattr(state, "items", {}))
        known |= set(getattr(state, "lessons", {}))
        known |= set(getattr(state, "decisions", {}))
        known |= set(getattr(state, "research", {}))
        known |= set(getattr(state, "memories", {}))
        # Journal entries land as session NOTES, not as their own records, so "is this
        # already imported" cannot be answered by an id table. It is answered by the
        # `ident` each note carries -- without this a second import duplicated every
        # journal entry while reporting success, and the existing idempotency test never
        # saw it because its fixture had none.
        for sess in getattr(state, "sessions", {}).values():
            known |= {n.get("ident", "") for n in sess.notes if n.get("ident")}
    known.discard("")
    return known


def _pull_in_needed(
    plan: ImportPlan, deferred_done: dict[str, Found], held: list[str]
) -> tuple[int, int]:
    """Bring in finished/closed items that open work depends on. Returns (done, closed).

    A FINISHED dependency comes in as done, so the dependent is ready. A CLOSED one
    (declined, refuted) comes in as abandoned -- which never satisfies a dependency, so
    the dependent would sit blocked forever on "S.1 is abandoned" after a note promising
    the opposite (roborev 824). Such a dependent is HELD instead, saying which declined
    item it waits on: somebody has to decide whether the dependency still stands.
    """
    wanted = {d for f in plan.found for d in f.needs} & set(deferred_done)
    done = closed = 0
    for ident in sorted(wanted):
        plan.found.append(deferred_done[ident])
        if deferred_done[ident].done:
            done += 1
        else:
            closed += 1
    declined = {i for i in wanted if not deferred_done[i].done}
    for f in plan.found:
        if f.kind != "task" or f.done or f.extra.get("disposition"):
            continue
        waits = sorted(set(f.needs) & declined)
        if waits:
            f.extra["disposition"] = "hold"
            f.extra["disposition_why"] = (
                f"needs {', '.join(waits)}, which the source declines -- drop the "
                f"dependency and unblock, or abandon this too"
            )
            held.append(f.ident)
    if wanted:
        plan.notes.append(
            f"{len(wanted)} finished or declined task(s) were imported anyway because open "
            f"work depends on them: {', '.join(sorted(wanted))}. A finished one satisfies "
            f"the dependency; a declined one never can, so what waits on it is held."
        )
    return done, closed


def _note_withheld(
    plan: ImportPlan, ticked: list[Found], closed: list[str], held: list[str]
) -> None:
    """Say what the import deliberately did not offer as work, and how to get it."""
    if ticked:
        # Per phase, worst first: the total alone left "Phase 103: 0/3" on the board
        # reading as "nothing shipped" about a phase whose 103.A and 103.C had (B45d5aa72fa).
        per_phase: dict[str, int] = {}
        for f in ticked:
            key = f.extra.get("phase") or "(no phase)"
            per_phase[key] = per_phase.get(key, 0) + 1
        worst = sorted(per_phase.items(), key=lambda kv: -kv[1])
        plan.notes.append(
            f"{len(ticked)} already-ticked task(s) were NOT imported, so the board counts "
            f"only the work left: a phase reading 0/3 may have shipped most of itself. "
            f"Most left out: "
            + ", ".join(f"{p} ({n})" for p, n in worst[:_NOTE_EXAMPLES])
            + (f", ... ({len(worst)} phases)" if len(worst) > _NOTE_EXAMPLES else "")
            + ". Re-run with --include-done (include_done over MCP) to bring them in as "
            "completed items -- which also completes every phase they finish."
        )
    if closed:
        # Named, like the held ones below: a bare count hid that two items under a
        # `(not started)` heading had been dropped with the history around them.
        plan.notes.append(
            f"{len(closed)} open task(s) were NOT imported because the source disposes "
            f"of them (declined, refuted, superseded, struck through, or in a section whose "
            f"STATUS says it is closed). They are history — pass include_done to bring them "
            f"in as abandoned items. E.g. {', '.join(closed[:_NOTE_EXAMPLES])}"
            + (", ..." if len(closed) > _NOTE_EXAMPLES else "")
            + "."
        )
    if held:
        plan.notes.append(
            f"{len(held)} open task(s) are marked deferred, theoretical or blocked (on the "
            f"item, its heading or its section's STATUS) and import as BLOCKED with that "
            f"reason: visible, never offered, and released with `ddflow unblock <id>`. "
            f"E.g. {', '.join(held[:_NOTE_EXAMPLES])}"
            + (", ..." if len(held) > _NOTE_EXAMPLES else "")
            + "."
        )
    if plan.empty_sources:
        # In the NOTES, not only in `empty_sources`: the human-readable proposal never
        # printed that field, so pointing `todo_globs` at a backlog written as bold
        # bullets instead of checkboxes printed a clean-looking proposal with nothing
        # from it, and the footer still said "the headings became phases".
        plan.source_notes.append(
            f"{len(plan.empty_sources)} file(s) matched a source pattern and yielded "
            f"NOTHING — usually an unusual format (a plan with no `- [ ]` checkboxes, "
            f"lessons with no headings) rather than an empty file: "
            f"{', '.join(plan.empty_sources[:_NOTE_EXAMPLES])}"
            + (", ..." if len(plan.empty_sources) > _NOTE_EXAMPLES else "")
        )


#: What the importer itself writes about an item: the add, carrying its source, and the
#: state the source gave it, flagged `imported`. Anything else on an item's subject is
#: somebody acting on it after the import.
_IMPORT_ADDS = frozenset({"phase.added", "task.added"})
_IMPORT_STATES = frozenset({"item.completed", "item.abandoned", "item.blocked"})
#: Every event that sets an item's state (see `core.model.HANDLERS`).
_STATE_EVENTS = _IMPORT_STATES | {"item.started", "item.unblocked"}


@dataclass
class Touched:
    """What somebody other than the importer did to the queue, per item id."""

    #: Any event at all on it that the importer did not write: claimed, gated, updated,
    #: blocked and released, completed -- or created by hand in the first place.
    items: set[str] = field(default_factory=set)
    #: Items whose CURRENT state was set by such an event: the last state change on
    #: them is somebody's, not the import's.
    state: set[str] = field(default_factory=set)


def _by_import(ev) -> bool:
    if ev.kind in _IMPORT_ADDS:
        return bool(ev.data.get("source"))
    return ev.kind in _IMPORT_STATES and bool(ev.data.get("imported"))


def touched_since_import(events) -> Touched:
    """Who acted on each item after (or instead of) an import, read from the log.

    What "already in the queue, left alone" has to mean once a re-run may COMPLETE an
    imported phase: the import may finish what it wrote and nobody has touched since, and
    nothing else. The fold cannot tell "open because nobody got to it" from "open because
    somebody reopened it"; the log can. `events` in log order, as `read_all` returns them.
    """
    t = Touched()
    for ev in events:
        mine = _by_import(ev)
        if not mine:
            t.items.add(ev.subject)
        if ev.kind in _STATE_EVENTS:
            (t.state.discard if mine else t.state.add)(ev.subject)
    return t


#: Words in a phase HEADING that say work remains, matched as whole words (`WIP` is not
#: `WIPE`). Two strengths. A STALLED word yields to a done marker in the same heading --
#: `Close the long-context PARTIAL ✅ SHIPPED` is finished, `DEFERRED` often says where
#: the work came from. A LIVE word does not: `Tooling (IN PROGRESS) ✅` contradicts
#: itself, and the side that hides work is the one not to pick (rubber-duck).
_HEADING_LIVE = re.compile(
    r"(?<![A-Z0-9])(IN PROGRESS|WIP|RE-?OPENED|UNFINISHED)(?![A-Z0-9])", re.I
)
_HEADING_STALLED = re.compile(
    r"(?<![A-Z0-9])(PARTIAL(?:LY)?|DEFERRED|ON HOLD|PARKED|BLOCKED)(?![A-Z0-9])", re.I
)
#: A done marker the heading itself negates: `NOT DONE`, `not yet shipped`.
_NEGATED_DONE = re.compile(rf"{_NEGATION}\W*(?:DONE|SHIPPED|CLOSED|COMPLETE[D]?)\b", re.I)


def _claims_done(title: str) -> bool:
    """Whether a heading says its work is finished: some status in it is not negated.

    One place for the drift note, `import --verify` and the phase verdict. Negation is
    per status, not per heading: `Phase 12 SHIPPED — docs NOT DONE` still claims done
    (so a phase shipped with a task open is still drift), `Phase 9 (NOT YET SHIPPED)`
    does not. Whether the heading ALSO says something remains is `_says_unfinished`'s
    question, built from the same `_NEGATION`: that one keeps the verdict from
    completing such a phase. In a heading written in
    capitals a capital status word must end its clause (`_DONE_CAPS_ENDING`).
    """
    letters = [c for c in title if c.isalpha()]
    caps_heading = bool(letters) and all(c.isupper() for c in letters)
    caps = _DONE_CAPS_ENDING if caps_heading else _DONE_CAPS
    starts = [m.start() for m in caps.finditer(title)]
    starts += [m.start("w") for m in _DONE_CLAUSE.finditer(title)]
    starts += [m.start() for m in _DONE_CHECK.finditer(title)]
    return any(not _NEGATED_BEFORE.search(title[:at]) for at in starts)


def _says_unfinished(phase: Found) -> str:
    """Why the phase's own words say work remains, or "" when they do not.

    Every box ticked is not the whole story: the project writes the rest in prose, and a
    real plan has `## Phase 1 — Tooling (IN PROGRESS)` and `**STATUS**: PARTIAL -- 11 of
    12` over nothing but ticked boxes. Completing those hides the one item left.
    """
    status = phase.extra.get("status")
    if status is not None and status[0] != "closed":
        return f"its STATUS says {status[1] or 'it is live'}"
    m = _NEGATED_DONE.search(phase.title) or _HEADING_LIVE.search(phase.title)
    if not m and not _claims_done(phase.title):
        m = _HEADING_STALLED.search(phase.title)
    return f"its heading says {m.group(0).upper()}" if m else ""


def _phase_verdict(
    phase: Found, pid: str, source: str, new_kids: list[Found], state, touched: Touched
) -> tuple[str, str]:
    """`(verdict, detail)` for one phase: `done`, `by_hand`, `unfinished` or `open`.

    Over EVERY task under it once this import has run: the ones it adds and the ones
    already in the queue. Done when each is done or closed; a phase with no task at all
    only when its own heading says finished. A queued task finished by somebody in
    ddflow rather than by the source makes it `by_hand`: closing the phase then is
    ddflow's own `complete`, with the phase gates, not the importer's. Boxes that all say
    finished under a heading or STATUS that says otherwise make it `unfinished`.
    """
    done = closed = 0
    by_hand = False
    for f in new_kids:
        if f.done:
            done += 1
        elif f.extra.get("disposition") == "closed":
            closed += 1
        else:
            return "open", ""
    for it in state.children(pid) if state is not None else ():
        if it.removed:
            continue
        if it.state not in (DONE, ABANDONED):
            return "open", ""
        if it.id in touched.state:
            by_hand = True
        elif it.state == DONE:
            done += 1
        else:
            closed += 1
    if by_hand:
        return "by_hand", ""
    if done + closed and _says_unfinished(phase):
        return "unfinished", _says_unfinished(phase)
    if done + closed:
        return "done", (
            f"every task under it is done or closed in the source ({done} done, "
            f"{closed} closed) -- phase imported from {source}"
        )
    if _claims_done(phase.title) and not _says_unfinished(phase):
        return "done", f"its heading at {source} marks it finished, and no task is filed under it"
    return "open", ""


@dataclass
class _Settled:
    """What `_settle_phases` decided, kept for the notes that say so."""

    new_done: int = 0
    completions: list[Found] = field(default_factory=list)
    left_alone: list[str] = field(default_factory=list)
    by_hand: list[str] = field(default_factory=list)
    unfinished: list[str] = field(default_factory=list)
    under_done: list[str] = field(default_factory=list)


def _is_imported_phase(it) -> bool:
    """A phase the IMPORTER wrote -- the only kind it may ever complete."""
    return it is not None and it.kind == "phase" and not it.removed and bool(it.source)


def _settle_existing(
    s: _Settled, existing: dict[str, Found], kids: dict[str, list[Found]], state, touched: Touched
) -> None:
    items = getattr(state, "items", {}) if state is not None else {}
    for pid, f in existing.items():
        it = items.get(pid)
        if not _is_imported_phase(it):
            continue
        if it.state == DONE:
            if any(
                not (k.done or k.extra.get("disposition") == "closed") for k in kids.get(pid, [])
            ):
                s.under_done.append(pid)
            continue
        if it.state != OPEN:
            continue
        verdict, detail = _phase_verdict(f, pid, it.source, kids.get(pid, []), state, touched)
        if verdict == "by_hand":
            s.by_hand.append(pid)
        elif verdict == "unfinished":
            s.unfinished.append(f"{pid} ({detail})")
        elif verdict == "done" and pid in touched.items:
            s.left_alone.append(pid)
        elif verdict == "done":
            s.completions.append(
                Found(
                    kind="completion",
                    ident=pid,
                    title=it.title,
                    source=it.source,
                    done=True,
                    extra={"evidence": detail},
                )
            )


def _settle_phases(plan: ImportPlan, state, touched: Touched, existing: dict[str, Found]) -> None:
    """Complete the phases the source says are finished -- decided AFTER the tasks.

    New phases in the plan are marked done in place. A phase already in the queue gets a
    `completion` entry, and only if the import wrote it, it is still OPEN and nobody has
    touched it since (`touched_since_import`); one that would qualify but was touched is
    NAMED, not completed. A task under it counts as finished by the SOURCE only while its
    state is still the one the import gave it. Never reopens anything.
    """
    kids: dict[str, list[Found]] = {}
    for f in plan.found:
        if f.kind == "task" and f.extra.get("phase"):
            kids.setdefault(f.extra["phase"], []).append(f)
    s = _Settled()
    for f in plan.by_kind("phase"):
        verdict, detail = _phase_verdict(
            f, f.ident, f.source, kids.get(f.ident, []), state, touched
        )
        if verdict == "done":
            f.done = True
            f.extra["evidence"] = detail
            s.new_done += 1
        elif verdict == "by_hand":
            s.by_hand.append(f.ident)
        elif verdict == "unfinished":
            s.unfinished.append(f"{f.ident} ({detail})")
    _settle_existing(s, existing, kids, state, touched)
    plan.found.extend(s.completions)
    # Completed, so no longer "left alone": the preview's count of those stays true.
    completed = {c.ident for c in s.completions}
    plan.skipped_existing = [i for i in plan.skipped_existing if i not in completed]
    _note_settled(plan, s)


def _settle_needed_phases(
    plan: ImportPlan,
    deferred: dict[str, Found],
    state,
    touched: Touched,
    existing: dict[str, Found],
) -> None:
    """Complete, on a PLAIN import, the finished phases that open work depends on.

    A plain import keeps such a phase (dropping it would leave the dependency unknown) but
    leaves its ticked tasks out -- so it landed EMPTY and OPEN, and the dependent was never
    offered: "phase P5 has no open tasks but is not marked done" (B-import-empty-needed-
    phase). The boxes under it are still the source's evidence that it is finished, so the
    verdict is the one `--include-done` reaches, read from the tasks this import left out;
    the tasks themselves stay out, as a plain import promises. Only phases that open work
    NEEDS, judged over every task under them: every other phase is exactly as it was. A phase already in the queue is completed under `_settle_existing`'s rules
    (imported, still open, untouched since), so a re-run releases work an earlier import
    left stuck.
    """
    needed = {d for f in plan.found for d in f.needs}
    # ...and what work ALREADY in the queue needs: on a re-run the dependent was imported
    # last time, so it is not in this plan, and it is the one stuck.
    for it in getattr(state, "items", {}).values():
        if it.state not in (DONE, ABANDONED) and not it.removed:
            needed.update(it.needs)
    # Judged over EVERY task under the phase once this import has run -- the ones in this
    # plan (open ones keep it open; a finished one pulled in as a dependency counts as
    # done) and the ticked ones it leaves out -- plus, inside `_phase_verdict`, the ones
    # already queued. Skipping a phase because the plan held a task under it left it
    # empty-open whenever that task was itself a pulled-in dependency (roborev 959).
    planned = {id(f) for f in plan.found}
    kids: dict[str, list[Found]] = {}
    for f in plan.found:
        if f.kind == "task" and f.extra.get("phase"):
            kids.setdefault(f.extra["phase"], []).append(f)
    for f in deferred.values():
        if f.extra.get("phase") and id(f) not in planned:
            kids.setdefault(f.extra["phase"], []).append(f)
    s = _Settled()
    for f in plan.by_kind("phase"):
        if f.ident not in needed or f.done:
            continue
        verdict, detail = _phase_verdict(
            f, f.ident, f.source, kids.get(f.ident, []), state, touched
        )
        if verdict == "done":
            f.done = True
            f.extra["evidence"] = detail
            s.new_done += 1
        elif verdict == "unfinished":
            s.unfinished.append(f"{f.ident} ({detail})")
    stuck = {pid: f for pid, f in existing.items() if pid in needed}
    _settle_existing(s, stuck, kids, state, touched)
    plan.found.extend(s.completions)
    completed = {c.ident for c in s.completions}
    plan.skipped_existing = [i for i in plan.skipped_existing if i not in completed]
    _note_settled(plan, s)


def _note_settled(plan: ImportPlan, s: _Settled) -> None:
    def named(ids: list[str]) -> str:
        return ", ".join(ids[:_NOTE_EXAMPLES]) + (", ..." if len(ids) > _NOTE_EXAMPLES else "")

    if s.new_done or s.completions:
        plan.notes.append(
            f"{s.new_done} new phase(s) and {len(s.completions)} already in the queue are "
            f"finished in the source -- every task under them done or closed, or no task "
            f"and a heading that says so -- and import COMPLETED, with that as evidence."
        )
    if s.left_alone:
        plan.notes.append(
            f"{len(s.left_alone)} imported phase(s) are finished in the source but were left "
            f"open because someone changed them after the import: {named(s.left_alone)}. "
            f"`ddflow complete <id>` if they are done."
        )
    if s.by_hand:
        plan.notes.append(
            f"{len(s.by_hand)} phase(s) have every task finished, some of them in ddflow "
            f"rather than in the source, so the import does not close them -- that is "
            f"`ddflow complete <phase>`, with its phase gates: {named(s.by_hand)}."
        )
    if s.unfinished:
        plan.notes.append(
            f"{len(s.unfinished)} phase(s) have every box ticked or closed while their own "
            f"heading or STATUS says work remains, so they stay OPEN: {named(s.unfinished)}. "
            f"Ask the operator which is stale; `ddflow complete <id>` if they are done."
        )
    if s.under_done:
        plan.notes.append(
            f"{len(s.under_done)} phase(s) are DONE in the queue while this import adds open "
            f"work under them: {named(s.under_done)}. Reopen the phase or re-home the work."
        )


#: A scanner: repo and globs in, `(found, files that matched and yielded nothing)` out.
Scanner = Callable[..., tuple[list[Found], list[str]]]


def _scanners() -> tuple[tuple[str, Scanner, tuple[str, ...]], ...]:
    """family -> (scanner, the globs it reads when `[importer] <family>_globs` is empty).

    ONE table: `plan_import`, the handshake's "is there anything to import?" count and the
    config knobs all read it, so a family added here is configurable and counted without
    anyone remembering to. A function rather than a constant only because the scanners are
    defined above it and the table must follow them.
    """
    return (
        ("todo", scan_todos, TODO_GLOBS),
        ("lesson", scan_lessons, LESSON_GLOBS),
        ("lesson_summary", scan_lesson_summaries, LESSON_SUMMARY_GLOBS),
        ("decision", scan_decisions, DECISION_GLOBS),
        ("research", scan_research, RESEARCH_GLOBS),
        ("journal", scan_journal, JOURNAL_GLOBS),
        ("memory", scan_optmem, OPTMEM_GLOBS),
    )


def sources_from(cfg) -> dict[str, tuple[str, ...]]:
    """The globs each family reads under this config: the knob when set, else the default.

    A set knob REPLACES the default rather than extending it. That is what lets a project
    whose `docs/LOG.md` is a generated index of its real journal leave it out, and a
    project whose `docs/LOG.md` IS the journal put it in -- the same filename meaning
    opposite things in the two repositories this was built against.

    Spelled out knob by knob rather than read with `getattr(f"{family}_globs")`: a
    family added to `_scanners()` with no knob behind it is then a KeyError here, not a
    silently unconfigurable source.
    """
    imp = cfg.importer
    configured = {
        "todo": imp.todo_globs,
        "lesson": imp.lesson_globs,
        "lesson_summary": imp.lesson_summary_globs,
        "decision": imp.decision_globs,
        "research": imp.research_globs,
        "journal": imp.journal_globs,
        "memory": imp.memory_globs,
    }
    return {
        family: tuple(configured[family] or ()) or default for family, _scan, default in _scanners()
    }


def all_source_globs(cfg=None) -> tuple[str, ...]:
    """Every file pattern the scanners read under `cfg` (the defaults without one)."""
    srcs = sources_from(cfg) if cfg is not None else {f: d for f, _s, d in _scanners()}
    return tuple(g for globs in srcs.values() for g in globs)


def plan_import(
    repo: Path,
    state=None,
    *,
    include_done: bool = False,
    max_tasks: int = 200,
    sources: dict[str, tuple[str, ...]] | None = None,
    archive: tuple[str, ...] = (),
    events: list | None = None,
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
    when the history itself is what you want. Open items the source itself DISPOSES of
    (declined, refuted, struck through) are history too and follow the same rule; items
    it DEFERS import as blocked.

    ``max_tasks`` is a guard rail rather than a policy: an import that silently writes
    five thousand events into a log that is committed to git is not recoverable by
    anything short of editing history. Over the cap, the plan reports the overflow and
    refuses to propose it — narrow the scope, or raise the cap deliberately.

    ``sources`` maps a family (`todo`, `lesson`, ...) to the globs to read for it; a
    family absent from it reads its defaults. `sources_from(cfg)` builds it from config.
    ``archive`` names todo files whose open items are history until released: they import
    as BLOCKED (`[importer] archive_globs`).

    With ``include_done`` a phase whose every task is done or closed is proposed DONE --
    and an imported phase already in the queue gets a `completion` once the re-run has
    filled it in (`_settle_phases`). ``events`` is the log ``state`` was folded from, which
    says whether somebody touched such a phase since; read from ``repo`` when omitted.
    """
    plan = ImportPlan()
    known = _known_ids(state)
    sources = sources or {}

    closed: list[Found] = []
    held: list[str] = []
    deferred_done: dict[str, Found] = {}
    proposed: set[str] = set()
    existing_phases: dict[str, Found] = {}
    scanned: list[Found] = []
    for family, scan, default in _scanners():
        globs = sources.get(family) or default
        items, empty = scan(repo, globs, archive) if family == "todo" else scan(repo, globs)
        plan.empty_sources.extend(empty)
        scanned.extend(items)
    _attach_summaries(scanned, plan)
    for f in scanned:
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
        if f.ident in known or f.extra.get("legacy_ident") in known:
            plan.skipped_existing.append(f.ident)
            if f.kind == "phase":
                existing_phases[f.ident] = f
            continue
        if f.kind == "task" and f.done and not include_done:
            deferred_done[f.ident] = f
            continue
        if f.kind == "task" and f.extra.get("disposition") == "closed" and not include_done:
            # Declined, refuted, superseded: history, the same as a ticked box -- and
            # brought in the same way when open work depends on it.
            deferred_done[f.ident] = f
            closed.append(f)
            continue
        if f.kind == "task" and f.extra.get("disposition") == "hold":
            held.append(f.ident)
        plan.found.append(f)

    # A finished task that an OPEN task depends on has to come too, as done. Skipping
    # it leaves the open one blocked on an id the queue has never heard of — and an
    # unknown dependency is treated as unmet, deliberately, so the import would land
    # permanently stuck work and look like it had succeeded.
    _pull_in_needed(plan, deferred_done, held)
    in_plan = {id(f) for f in plan.found}
    ticked = [f for f in deferred_done.values() if f.done and id(f) not in in_plan]
    plan.ticked_left_out = len(ticked)
    _note_withheld(plan, ticked, [f.ident for f in closed if id(f) not in in_plan], held)

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
        if events is None and state is not None:
            # The same `[log]` config every other reader honours (the parse cache).
            events = EventLog(repo, log_cfg=Config.load(repo).log).read_all()
        touched = touched_since_import(events or ())
        if include_done:
            # AFTER the tasks are in the plan: a phase is finished or not by what is
            # under it once this import has run, not by what was under it before.
            _settle_phases(plan, state, touched, existing_phases)
        else:
            _settle_needed_phases(plan, deferred_done, state, touched, existing_phases)
    _dedupe_found(repo, state, plan)
    for f in scan_branches(repo):
        f.ident = _unique("", f.ident, proposed)
        if f.ident not in known:
            plan.found.append(f)

    if not plan.found and not plan.skipped_existing and not plan.duplicates:
        plan.notes.append(
            "Nothing recognisable was found. That is not necessarily wrong — this looks "
            "for todo checklists, a lessons corpus, ADR files and unmerged branches in "
            "their usual locations. If this project keeps them somewhere else, tell the "
            "agent where and it can file them with `ddflow task add` / `lesson add` / "
            "`decision add` directly."
        )
    return plan


@dataclass
class VerifyReport:
    """What an import left behind, and whether it is still true.

    Three questions, and they cost different amounts, which is why they are separate
    fields rather than one verdict:

    * **status** -- what is imported, per kind. Read from the folded queue; free.
    * **still true?** -- has the source moved since. Needs a fresh scan of every source
      file (~0.65 s on a 4,799-checkbox corpus), so it is done here and NOT at the MCP
      handshake.
    * **finished?** -- the judgement half the `import-existing-project` prompt asks a
      human for, which nothing checked until this existed. An imported queue nobody
      finished misrepresents the project exactly as an empty one does, and is believed
      harder because a tool produced it.

    Deliberately does NOT re-report what `ddflow doctor` already covers -- unresolved
    dependencies, duplicate globs, cycles. Two commands reporting one defect in
    different words is how an operator learns to read neither.
    """

    #: kind -> how many items of it carry import provenance.
    imported: dict[str, int] = field(default_factory=dict)
    #: Earliest and latest `created_at` among imported items; "" when none.
    first_at: str = ""
    last_at: str = ""
    #: What a re-run would add now, because the source files moved on.
    drift: list[Found] = field(default_factory=list)
    #: Matched a source pattern and yielded nothing -- usually an unusual format
    #: rather than an empty file, which is why it is reported and not ignored.
    empty_sources: list[str] = field(default_factory=list)
    #: `(item id, source)` for EVERY item whose file is gone -- not one per file. Built
    #: per-file and reported per-item, four items sharing `docs/todo.md` came out as one
    #: entry, and an agent auditing provenance fixes the one it was told about and
    #: believes it has finished.
    vanished: list[tuple[str, str]] = field(default_factory=list)
    #: Imported tasks with no globs. The conflict detector cannot protect them, so two
    #: agents can be handed the same file and neither is refused.
    no_globs: list[str] = field(default_factory=list)
    #: Imported BRANCHES with no globs, kept apart from `no_globs`. A branch arrives
    #: without them by construction -- git knows which commits it carries, not which
    #: files the work will touch -- so counting it as a task that failed to declare
    #: made the arithmetic lie ("2 task(s) declare no globs" under "1 branch, 2 tasks")
    #: and made the handshake say "never finished" forever about any repo with an
    #: unmerged branch.
    no_globs_branches: list[str] = field(default_factory=list)
    #: Phases whose title still claims the work shipped while a task under them is open.
    shipped_drift: list[str] = field(default_factory=list)
    #: Items carrying the prose "Imported from ..." body but no `source` FIELD --
    #: written by a version before the field existed. Reported as unknown rather than
    #: guessed at by regexing the sentence.
    unstructured: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(self.imported.values())

    @property
    def findings(self) -> list[str]:
        """One line per thing a human has to decide or fix. Empty means consistent."""
        out: list[str] = []
        if self.no_globs:
            out.append(
                f"{len(self.no_globs)} imported task(s) declare no globs, so the "
                f"conflict detector cannot protect them and two agents can be handed "
                f"the same file: {', '.join(self.no_globs[:_NOTE_EXAMPLES])}"
                + (", ..." if len(self.no_globs) > _NOTE_EXAMPLES else "")
            )
        if self.shipped_drift:
            out.append(
                f"{len(self.shipped_drift)} phase(s) say the work is finished while a "
                f"task under them is still open: "
                f"{', '.join(self.shipped_drift[:_NOTE_EXAMPLES])}"
                + (", ..." if len(self.shipped_drift) > _NOTE_EXAMPLES else "")
                + ". Ask the operator which is stale before anyone claims from them."
            )
        if self.drift:
            out.append(
                f"the source has moved on: {len(self.drift)} item(s) exist in the files "
                f"that are not in the queue. Re-run the import to pick them up."
            )
        if self.vanished:
            files = sorted({src for _i, src in self.vanished})
            out.append(
                f"{len(self.vanished)} imported item(s) across {len(files)} file(s) "
                f"name a source that no longer exists, so their provenance cannot be "
                f"checked: "
                + ", ".join(f"{i} ({src})" for i, src in self.vanished[:_NOTE_EXAMPLES])
                + (", ..." if len(self.vanished) > _NOTE_EXAMPLES else "")
            )
        if self.empty_sources:
            out.append(
                f"{len(self.empty_sources)} file(s) matched a source pattern and "
                f"yielded nothing, which usually means an unusual format rather than an "
                f"empty file: {', '.join(self.empty_sources[:_NOTE_EXAMPLES])}"
            )
        if self.no_globs_branches:
            out.append(
                f"{len(self.no_globs_branches)} imported branch(es) declare no globs. A "
                f"branch arrives without them -- git knows its commits, not which files "
                f"the work will touch -- so somebody has to say before it is claimed: "
                f"{', '.join(self.no_globs_branches[:_NOTE_EXAMPLES])}"
            )
        return out


def _scan_queue(state, r: VerifyReport) -> tuple[list[str], dict[str, list[str]]]:
    """Everything answerable from the folded queue. Touches no file.

    Returns `(created_at values, source path -> item ids)` for the caller's rescan.
    """
    ats: list[str] = []
    paths: dict[str, list[str]] = {}
    for it in getattr(state, "items", {}).values():
        if it.removed:
            continue
        if not it.source:
            # The prose body is the only remaining signal for an item imported before
            # provenance was a field. COUNTED, never parsed: a body is a sentence
            # someone may reword, and a count that silently becomes zero when they do
            # is worse than an admitted unknown.
            if it.body.startswith("Imported from "):
                r.unstructured.append(it.id)
            continue
        r.imported["branch" if it.source.startswith("git:") else it.kind] = (
            r.imported.get("branch" if it.source.startswith("git:") else it.kind, 0) + 1
        )
        if it.created_at:
            ats.append(it.created_at)
        if it.kind == "task" and not it.globs and it.state not in (DONE, ABANDONED):
            (r.no_globs_branches if it.source.startswith("git:") else r.no_globs).append(it.id)
        elif it.kind == "phase" and _claims_done(it.title) and _has_open_child(state, it):
            r.shipped_drift.append(it.id)
        if not it.source.startswith("git:"):
            paths.setdefault(it.source.split(":", 1)[0], []).append(it.id)

    for kind, holder in (
        ("lesson", getattr(state, "lessons", {})),
        ("decision", getattr(state, "decisions", {})),
        ("research", getattr(state, "research", {})),
    ):
        n = sum(1 for rec in holder.values() if "imported" in getattr(rec, "tags", []))
        if n:
            r.imported[kind] = n
    _note_sources(state, r, paths)
    imported_memories = [m for m in getattr(state, "memories", {}).values() if m.source]
    legacy = getattr(state, "sessions", {}).get(LEGACY_MEMORY_SESSION)
    legacy_notes = [n for n in (legacy.notes if legacy else []) if n.get("source")]
    if imported_memories or legacy_notes:
        r.imported["memory"] = len(imported_memories) + len(legacy_notes)
    for m in imported_memories:
        rel = m.source.split(":", 1)[0]
        if rel:
            paths.setdefault(rel, []).append(m.id)
    prose = _memory_sources(state, paths)
    if prose:
        # A NOTE: nobody looked at these, and a clean report must not read as if they
        # were checked and found present.
        r.notes.append(
            f"{prose} imported source(s) are prose that mentions a path rather than a "
            f"path, so they were not checked for a vanished file."
        )
    return ats, paths


def _note_sources(state, r: VerifyReport, paths: dict[str, list[str]]) -> None:
    """Imported notes, counted and their sources collected.

    Filtered on `source`, exactly as the memory records are filtered on the `imported`
    tag. `ddflow session note <sid>` takes ANY session id, so the import's own sinks
    are not private to it -- one hand-written note in `s-imported-journal` counted as
    an imported record, which is the missing-provenance-filter bug in the function
    written to stop guessing at provenance.
    """
    for kind, sid, _what in NOTE_SESSIONS:
        sess = getattr(state, "sessions", {}).get(sid)
        if not sess:
            continue
        imported = [n for n in sess.notes if n.get("source")]
        if imported:
            r.imported[kind] = len(imported)
        for n in imported:
            rel = str(n["source"]).split(":", 1)[0]
            if rel and not rel.startswith("git:"):
                paths.setdefault(rel, []).append(f"{sid}#{n.get('ident', n.get('seq', '?'))}")


def _memory_sources(state, paths: dict[str, list[str]]) -> int:
    """Source files named by imported lessons and research notes; returns how many
    sources mentioned a path but were prose, so were not checked.

    All three carry a STRUCTURED source -- `Lesson.seen_in`, `ResearchNote.sources`,
    `Decision.sources` -- so there is no reason for the vanished-source check to cover
    items only. Decisions were the gap until `Decision.sources` existed: theirs lived
    in the prose `context`, and parsing a path back out of a sentence is the
    anti-pattern this whole check exists to replace.
    """
    prose = 0
    for holder, attr in (
        (getattr(state, "lessons", {}), "seen_in"),
        (getattr(state, "research", {}), "sources"),
        (getattr(state, "decisions", {}), "sources"),
    ):
        for rec in holder.values():
            if "imported" not in getattr(rec, "tags", []):
                continue
            for src in getattr(rec, attr, []) or []:
                # A link's target is one token already -- `docs/a(b).md` included --
                # so only its `#anchor` goes and its length is left to check. Anything
                # else is unwrapped and must then be a single markup-free token.
                link = _MD_LINK.match(str(src))
                if link:
                    rel = link.group(1).split("#", 1)[0].split(":", 1)[0]
                    shaped = len(rel) <= _MAX_SOURCE_PATH
                else:
                    rel = _MARKUP_AROUND_PATH.sub("", str(src).split(":", 1)[0])
                    shaped = _path_shaped(rel)
                # Narrow on purpose: never `git:foo.md`, and a lesson's `seen_in` also
                # holds bug pins like "review-inverted-severity", which are not paths
                # -- reporting those as vanished files would be a false positive in the
                # check whose whole value is that its findings are real.
                looks_like_a_path = "/" in rel or rel.endswith(".md")
                if not rel or rel.startswith("git:") or not looks_like_a_path:
                    continue
                if shaped:
                    paths.setdefault(rel, []).append(rec.id)
                else:
                    prose += 1
    return prose


#: Longest free-form source still taken for a path. Far above any repository-relative
#: path a person writes, far below a paragraph.
_MAX_SOURCE_PATH = 1024
#: Markdown around a path in a `**Seen in:**` paragraph: "`nemorun/cli/export.py:88`."
#: is the file `nemorun/cli/export.py`, and reporting it vanished while it exists is a
#: false finding. A leading `.` is left alone -- `.ddflow/x.md` is a path.
_MARKUP_AROUND_PATH = re.compile(r"^[`'\"(\[<*]+|[`'\")\]>*.,;]+$")
#: A whole markdown link, `[docs/a.md](docs/a.md)`: its TARGET is the path -- also with
#: a title in any of CommonMark's three forms, and inside code/bold/emphasis markup with
#: punctuation on either side of it, `` `[a](docs/a.md 't').` ``, and a target with
#: balanced parentheses, `[a](docs/a(b).md)`.
_MD_LINK = re.compile(
    r"""^[`*_]*\[[^\]]*\]\(((?:[^()\s]|\([^()\s]*\))+)(?:\s+(?:"[^"]*"|'[^']*'|\([^)]*\)))?\)[`*_.,;]*$"""
)
#: Markup still inside a token once the wrapping is gone: not one path, but pieces.
_MARKUP_INSIDE = frozenset("`[]()<>*")


def _path_shaped(rel: str) -> bool:
    """One token that could name a file, not a sentence that mentions one (Bd5b59f3894).

    Only for the FREE-FORM fields `_memory_sources` reads: an imported lesson's `seen_in`
    is its corpus's `**Seen in:**` paragraph, and on a real corpus that is prose like
    "Phase 136, roborev 414-D4 ... `config/training/x.py` cited ~2839". It contains a
    `/`, so the old test took it for a path; a short one was reported as a vanished
    file, a long one crashed the stat. Item, note and memory sources are not filtered
    here: the importer writes them from real file names, which may contain spaces.
    """
    return len(rel) <= _MAX_SOURCE_PATH and not any(c.isspace() or c in _MARKUP_INSIDE for c in rel)


def _has_open_child(state, phase) -> bool:
    return any(c.state not in (DONE, ABANDONED) and not c.removed for c in state.children(phase.id))


def verify_import(
    repo: Path,
    state,
    *,
    rescan: bool = True,
    sources: dict[str, tuple[str, ...]] | None = None,
    archive: tuple[str, ...] = (),
) -> VerifyReport:
    """Was the import done, is it still true, and did anyone finish it?

    ``rescan=False`` answers only from the folded queue and touches no file. That is
    the mode the MCP handshake uses: a full source scan is cheap for a command an
    operator typed and expensive for something that runs at every session start.
    """
    r = VerifyReport()
    ats, paths = _scan_queue(state, r)
    r.first_at, r.last_at = (min(ats), max(ats)) if ats else ("", "")

    if r.unstructured:
        # A NOTE, not a finding. Its own text says re-running does not fix it, and a
        # finding nobody can clear is a latch: `verified` could never go true again, and
        # a report that can never go green is one people stop reading.
        r.notes.append(
            f"{len(r.unstructured)} item(s) were imported before provenance was a "
            f"field, so what they came from is readable only as prose in their body. "
            f"Nothing fixes that; it is history."
        )
    if not r.total and not r.unstructured:
        r.notes.append(
            "Nothing in this queue records an import. If this project has history in "
            "docs/todo.md, a lessons corpus, ADRs, a research log, an engineering "
            "journal or a memory store, `ddflow import` proposes it and writes "
            "nothing until you pass --apply."
        )
        return r
    if not rescan:
        return r

    unstatable: list[str] = []
    for rel, ids in sorted(paths.items()):
        # os.stat, not Path.is_file: 3.11-3.13 RAISE on ENAMETOOLONG, 3.14 returns False
        # for every OSError -- a crash on one, an "unstatable" reported as vanished on
        # the other. Only "no such file" is a vanished file.
        try:
            present = stat.S_ISREG(os.stat(repo / rel).st_mode)
        except (FileNotFoundError, NotADirectoryError):
            present = False
        except (OSError, ValueError):
            # ENAMETOOLONG, an embedded NUL and kin: the filesystem refuses the string as
            # a path at all. Not a vanished file -- nothing ever existed under that name
            # -- and never a crash: one odd record must not cost the whole report.
            unstatable.append(rel)
            continue
        if not present:
            r.vanished.extend((i, rel) for i in sorted(ids))
    if unstatable:
        r.notes.append(
            f"{len(unstatable)} imported source(s) could not be checked, because the "
            f"filesystem does not accept them as a path: "
            + ", ".join(repr(s[:60]) for s in unstatable[:_NOTE_EXAMPLES])
            + (", ..." if len(unstatable) > _NOTE_EXAMPLES else "")
        )

    plan = plan_import(repo, state, max_tasks=10**9, sources=sources, archive=archive)
    r.drift = list(plan.found)
    r.empty_sources = list(plan.empty_sources)
    # Minus the note this report states in its own terms (history left out); the
    # empty-source note is in `plan.source_notes` and never reaches here.
    r.notes.extend(n for n in plan.notes if "already-ticked" not in n)
    r.notes.append(
        "Dependencies that do not resolve, duplicate globs and cycles are `ddflow "
        "doctor`'s job and it reports them in its own words -- this does not repeat "
        "them."
    )
    return r


def _apply_state(log: EventLog, f: Found, bump: Callable[[str], None]) -> None:
    """The state an imported task lands in, beyond `task.added`: done, closed or held.

    Each carries its SOURCE in the reason -- ddflow does not invent completion, and a
    held item that cannot say why it is held is one nobody can decide to release.
    """
    if f.done:
        log.append(
            "item.completed",
            f.ident,
            {"kind": "task", "imported": True, "evidence": f"ticked in {f.source}"},
        )
        bump("task_done")
    elif f.extra.get("disposition") == "closed":
        log.append(
            "item.abandoned",
            f.ident,
            {
                "kind": "task",
                "imported": True,
                "reason": f"{f.extra.get('disposition_why', 'closed')} in {f.source}",
            },
        )
        bump("task_closed")
    elif f.extra.get("disposition") == "hold":
        log.append(
            "item.blocked",
            f.ident,
            {
                "kind": "task",
                "imported": True,
                "reason": (
                    f"{f.extra.get('disposition_why', 'held')} in {f.source}. "
                    f"`ddflow unblock {f.ident}` when it becomes work."
                ),
            },
        )
        bump("task_held")


def _resolve_chains(dropped: dict[int, Duplicate], in_plan: dict[str, Found]) -> None:
    """A summary bullet that repeats a record of this import which is itself left out
    repeats what THAT repeats: point at the record that is actually held, so the outcome
    does not depend on the order the records were read."""
    # One hop is all there is: only a summary-born lesson targets a record of this import,
    # and that record is never summary-born, so it was dropped against the queue.
    for d in dropped.values():
        nxt = dropped.get(id(in_plan[d.of])) if d.where == "import" else None
        if nxt is not None:
            d.of, d.where = nxt.of, nxt.where
            # Word for word only if every hop was; the score is the weakest hop's.
            d.identical = d.identical and nxt.identical
            d.score = min(d.score, nxt.score)


#: Marks a record of THIS import in the similarity index (see `_dedupe_found`).
_THIS_IMPORT = "\x00import:"

#: Identical repeats named in the plan's note (the rest are counted).
_SHOWN_IDENTICAL = 12


def _dedupe_found(repo: Path, state, plan: ImportPlan) -> None:
    """Leave out what repeats a record already held, and say so. In place.

    Decision D-no-duplicates, applied to the importer. Checked: lessons, decisions and
    research -- the knowledge an onboarding brings in. Tasks and phases are not: they
    carry dependencies, and withholding one would leave the rest waiting on an id the
    queue never heard of. Each is weighed against every record already in the log
    (`services.similar`, the same engine and thresholds `[dedupe]` sets for an add), and
    a lessons-summary bullet that became a lesson of its own is also weighed against the
    other records of this import -- that is how a hand-written summary repeats the
    corpus beside it.

    Identical text is dropped quietly (counted). A near-duplicate -- at `ask_threshold`
    with enough content words -- is dropped from the proposal and LISTED, with the id it
    repeats and the score, because no score separates a duplicate from a related record
    and the operator answers that. Naming an existing id does not count: an imported
    lesson citing `L12` is not a copy of it.
    """
    from ..infra.store import similar_records
    from . import similar

    cfg = Config.load(repo)
    dd = cfg.dedupe
    checked = ("lesson", "decision", "research")
    if dd.on_match == "off":
        return
    mine = [f for f in plan.found if f.kind in checked and f.kind in dd.kinds]
    if not mine:
        return
    base = similar_records(state) if state is not None else []

    def rec(f: Found) -> dict[str, str]:
        # Marked, so a record of this import never shares an id with a stored one (a
        # bug or an item can carry the same word): the marker says which side a
        # candidate came from.
        return {
            "id": _THIS_IMPORT + f.ident,
            "kind": f.kind,
            "title": f.title,
            "body": f.body,
            "item": "",
        }

    # Another record of this import is a target only for a summary-born lesson, and a
    # target must outrank it: a summary bullet repeats the corpus, never the reverse.
    # Two non-summary records of one import are NOT compared with each other, on purpose:
    # which of a pair to keep is the author's call (both are kept), and dropping the later
    # would make the outcome depend on the order the files were read.
    def is_summary(f: Found) -> bool:
        return f.kind == "lesson" and "summary" in f.extra.get("tags", ())

    index = similar.build([*base, *(rec(f) for f in mine)])
    in_plan = {f.ident: f for f in mine}
    dropped: dict[int, Duplicate] = {}
    for f in mine:
        a = similar.assess(index, rec(f), cfg)
        for c in a.candidates:
            tgt = in_plan.get(c.id.removeprefix(_THIS_IMPORT))
            where = "queue"
            if c.id.startswith(_THIS_IMPORT) and tgt is not None:
                where = "import"
                if not is_summary(f) or is_summary(tgt):
                    continue
            ident = "identical" in c.flags
            if similar.is_duplicate(c, a, cfg):
                dropped[id(f)] = Duplicate(
                    f, c.id.removeprefix(_THIS_IMPORT), c.score, ident, where
                )
                break
    if not dropped:
        return
    _resolve_chains(dropped, in_plan)
    warn = dd.on_match == "warn"
    if not warn:
        plan.found = [f for f in plan.found if id(f) not in dropped]
    plan.duplicates = [dropped[id(f)] for f in mine if id(f) in dropped]
    same = [d for d in plan.duplicates if d.identical]
    near = [d for d in plan.duplicates if not d.identical]
    if same:
        plan.notes.append(
            f"{len(same)} record(s) repeat another word for word and "
            f"{'WILL be imported anyway ([dedupe].on_match = warn)' if warn else 'were not imported'}: "
            + ", ".join(
                f"{d.found.ident} = {d.of}" + (" (in this import)" if d.where == "import" else "")
                for d in same[:_SHOWN_IDENTICAL]
            )
            + (" ..." if len(same) > _SHOWN_IDENTICAL else "")
        )
    if near:
        lines = [
            f"{len(near)} record(s) look like ones already held and "
            f"{'WILL be imported anyway ([dedupe].on_match = warn)' if warn else 'were NOT imported'} -- "
            f"decide each (file one anyway with the matching `ddflow <kind> add`, answering "
            f"its duplicate check with --new; "
            f"otherwise the existing record already says it). Candidate and score:"
        ]
        lines += [
            f"    {d.found.kind} {d.found.ident} ~ {d.of} ({d.score:.2f}"
            f"{', in this import' if d.where == 'import' else ''})  {d.found.source}"
            for d in sorted(near, key=lambda d: (-d.score, d.found.ident))
        ]
        plan.notes.append("\n".join(lines))


def apply_import(repo: Path, log: EventLog, plan: ImportPlan) -> dict[str, int]:
    """Write the proposal to the log. Called only after someone has looked at it.

    Tasks marked done are recorded as done **with their source**, not silently: the
    evidence is "a human ticked this box in docs/todo.md:41", which is exactly what it
    is, and a reader can go and check. ddflow does not invent completion.
    """
    counts: dict[str, int] = {}

    def bump(kind: str) -> None:
        counts[kind] = counts.get(kind, 0) + 1

    for f in plan.by_kind("phase"):
        log.append(
            "phase.added",
            f.ident,
            {"title": f.title, "body": f"Imported from {f.source}.", "source": f.source},
        )
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
                "resources": f.extra.get("resources", []),
                "body": f"Imported from {f.source}.",
                # Both: the prose is what a human reads in `ddflow show`, the field is
                # what `import --verify` counts. Deriving one from the other by regex is
                # how a reworded sentence silently zeroes a verification.
                "source": f.source,
            },
        )
        bump("task")
        _apply_state(log, f, bump)
    # Phases LAST: finished by what is under them, so only once that is written -- a
    # reader of the log never sees a phase done over tasks that do not exist yet.
    for f in [p for p in plan.by_kind("phase") if p.done] + plan.by_kind("completion"):
        log.append(
            "item.completed",
            f.ident,
            {"kind": "phase", "imported": True, "evidence": f.extra.get("evidence", "")},
        )
        bump("phase_done" if f.kind == "phase" else "completion")
    for f in plan.by_kind("lesson"):
        data: dict[str, Any] = {
            "title": f.title,
            "rule": f.body,
            "tags": ["imported", *f.extra.get("tags", [])],
            "seen_in": [f.source, *f.extra.get("seen_in", [])],
        }
        if f.extra.get("summary"):
            data["summary"] = f.extra["summary"]
        log.append("lesson.recorded", f.ident, data)
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
                "tags": ["imported"],
                "sources": [f.source],
            },
        )
        bump("decision")
    # Journal entries and OptMem records are both "a record of something that was
    # true", which is what a session note IS. One session each rather than one per
    # entry: N synthetic sessions would bury the real ones in `replay`.
    for kind, sid, what in NOTE_SESSIONS:
        entries = plan.by_kind(kind)
        if not entries:
            continue
        log.append("session.started", sid, {"model": "(imported)", "tool": "ddflow import"})
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
    # OptMem records are operational MEMORIES -- the thing `brief` shows first and
    # `recall` searches -- not journal notes. Their store numbered them, so the id is
    # the store's (`M-0041`) and a re-import skips what is already remembered.
    for f in plan.by_kind("memory"):
        log.append(
            "memory.recorded",
            f.ident,
            {
                "text": f.body[:4000],
                "origin_at": f.extra.get("at", ""),
                "source": f.source,
                "tags": ["imported"],
            },
        )
        bump("memory")
    for f in plan.by_kind("research"):
        log.append(
            "research.recorded",
            f.ident,
            {
                "question": f.title,
                "claim": f.body[:600],
                "verdict": f.extra.get("verdict", "THEORETICAL"),
                "sources": [f.source],
                "tags": ["imported"],
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
                "source": f.source,
            },
        )
        bump("branch")
    if plan.ticked_left_out:
        # Not written -- LEFT OUT, and said in the one line `--apply` prints, because
        # otherwise the report of what landed is read as the whole story (B45d5aa72fa).
        counts["ticked task(s) left out, see --include-done"] = plan.ticked_left_out
    return counts
