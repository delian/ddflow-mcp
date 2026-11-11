"""Title and prose parsing primitives (ids, done-words, dispositions, globs)."""

from __future__ import annotations

import re

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

#: `- [ ] **P1.T2** — do the thing` / `- [x] do the thing`. The `*`, `+` and numbered
#: (`1.` / `1)`) bullets are task-list items too and import with the same meaning.
_CHECK = re.compile(r"^(\s*)(?:[-*+]|\d{1,9}[.)])\s+\[( |x|X)\]\s+(.*)$")
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


#: How long an imported title may be. The slug lengths an id is derived from are the
#: `[importer] phase_slug_chars` / `task_slug_chars` knobs.
_TITLE_CHARS = 120
#: The slug lengths ids were derived with before they were cut at word boundaries. An
#: item imported then holds the old id, so a re-import must recognise it (`legacy_ident`).
_OLD_PHASE_SLUG, _OLD_TASK_SLUG = 24, 20
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9`\"'(\[])")
_BODY_CHARS = 4000


def _title_of(text: str, limit: int = _TITLE_CHARS) -> str:
    """A title that is a whole phrase: the first sentence of ``text`` when it fits, else
    the longest run of whole words that does, closed with an ellipsis and never ending in
    an open backtick."""
    text = " ".join(text.split())
    first = text
    for m in _SENTENCE_END.finditer(text):
        word = text[: m.start()].rsplit(" ", 1)[-1]
        # `39.` / `A.` / `e.g.` open a sentence, they do not end one.
        if re.fullmatch(
            r"[\d.]+|[A-Za-z]\.|(?:[A-Za-z]\.){2,}|(?i:no|dr|mr|mrs|ms|vs|etc|fig|cf)\.", word
        ):
            continue
        first = text[: m.start()]
        break
    return _cut_title(first, limit)


def _cut_title(text: str, limit: int = _TITLE_CHARS) -> str:
    """``text`` when it fits, else cut at the last word boundary within ``limit`` (at
    ``limit`` itself when one word is longer), closed with an ellipsis and never ending
    in an open backtick."""
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit)
    head = text[: cut if cut > 0 else limit - 1]
    if head.count("`") % 2 and head.rfind("`") > 0:
        head = head[: head.rfind("`")]
    return head.rstrip(" ,;:-\u2014\u2013(") + "\u2026"


#: A backticked token that is a repo path: has a directory part or a source-file suffix, no
#: spaces, not a URL. `pytest -q` and `self.x` are not.
_BACKTICKED = re.compile(r"`([^`\s]+)`")
#: `path.py:1219` and `path.py:12-30` cite a line; the glob is the file.
_LINE_SUFFIX = re.compile(r":\d+(?:[,-]\d+)*$")
_PATH_SUFFIX = (".py", ".md", ".toml", ".json", ".yaml", ".yml", ".ts", ".js", ".sh", ".cfg")
#: A prose dependency: the clause after 'Requires', 'Depends on', 'Needs', 'Blocked by'.
_PROSE_DEP = re.compile(r"\b(?:requires?|depends?\s+on|needs?|blocked\s+by)\b(?=(.{0,200}))", re.I)
#: 'no longer requires', 'does not need' / 'Requires no ...': the prose says the opposite.
_PROSE_NEGATED = re.compile(
    r"(?:n['\u2019]t|\b(?:no\s+longer|not|never))(?:\s+[\w'\u2019]+){0,2}\s*$", re.I
)
_PROSE_NEGATING = re.compile(
    r"[\s:]*(?:no(?!\s+(?:fewer|less|other))|nothing(?!\s+(?:but|except|other))|none|"
    r"not(?!\s+(?:only|just)))\b",
    re.I,
)
_ID_TOKEN = re.compile(r"[A-Za-z0-9_][\w.\-]*[\w]|\w")


def _prose_globs(text: str) -> list[str]:
    """Repo paths a task's prose names in backticks, in order, once each."""
    out: list[str] = []
    for raw in _BACKTICKED.findall(text):
        tok = _LINE_SUFFIX.sub("", raw).rstrip(".,;:")
        if "://" in tok or tok.startswith(("-", "$", "~")) or tok in out:
            continue
        head = tok.split("/", 1)[0]
        if "." in head and not head.startswith(".") and not head.endswith(_PATH_SUFFIX):
            continue  # `github.com/org/repo`: a host, not a path
        # A directory part alone is not enough: `and/or` and `read/write` are prose.
        if tok.endswith(_PATH_SUFFIX) or tok.endswith("/") or "*" in tok or tok.count("/") > 1:
            out.append(tok)
    return out


def _prose_needs(text: str, known: set[str], self_id: str) -> list[str]:
    """Ids this task's prose says it requires, kept only when the scan found such an
    item: an id nobody wrote would read as unmet and block the task for good."""
    out: list[str] = []
    for m in _PROSE_DEP.finditer(text):
        before = text[max(0, m.start() - 40) : m.start()]
        if _PROSE_NEGATED.search(before) or _PROSE_NEGATING.match(m.group(1)):
            continue
        # The keyword is matched zero-width, so the next phrase on the line is still found;
        # only the rest of THIS sentence: 'Requires no config. See 160.C.2.' requires nothing.
        clause = re.split(r"[.;:]\s", m.group(1).lstrip(": "), maxsplit=1)[0]
        for raw in _ID_TOKEN.findall(clause):
            tok = raw.strip(".,;")
            if tok in known and tok != self_id and tok not in out:
                out.append(tok)
    return out


def _continuation(lines: list[str], fenced: list[bool], n: int, indent: int) -> str:
    """The wrapped lines of the bullet that ends at line ``n`` (1-based): the indented
    lines after it, up to a blank line, a heading, a list item or an annotation."""
    out: list[str] = []
    for k in range(n, len(lines)):
        ln = lines[k]
        if fenced[k] or not ln.strip() or _HEADING.match(ln) or _CHECK.match(ln):
            break
        if len(ln) - len(ln.lstrip()) <= indent or re.match(r"\s*(?:[-*+]|\d{1,9}[.)])\s", ln):
            break
        if _GLOBS.search(ln) or _NEEDS.search(ln) or _RESOURCES.search(ln):
            break
        out.append(ln.strip())
    return " ".join(out)


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


__all__ = [
    "DECISION_GLOBS",
    "JOURNAL_GLOBS",
    "LESSON_GLOBS",
    "LESSON_SUMMARY_GLOBS",
    "OPTMEM_GLOBS",
    "RESEARCH_GLOBS",
    "SOURCE_GLOBS",
    "TODO_GLOBS",
    "_ANNOTATION_CHARS",
    "_ASIDE_CLAUSE",
    "_ASIDE_NOT_DISPOSITIONS",
    "_BACKTICKED",
    "_BODY_CHARS",
    "_BOLD",
    "_CHECK",
    "_CLAUSE_END",
    "_CLOSED_MARKERS",
    "_DECISION_INDEX_STEMS",
    "_DONE_CAPS",
    "_DONE_CAPS_ENDING",
    "_DONE_CHECK",
    "_DONE_CLAUSE",
    "_DONE_WORD",
    "_FUTURE_WORK",
    "_GLOBS",
    "_HEADING",
    "_HOLD_MARKERS",
    "_ID",
    "_ID_TOKEN",
    "_ITEM_ID",
    "_LEAD_WORD_CHARS",
    "_LINE_SUFFIX",
    "_NEEDS",
    "_NEGATED_BEFORE",
    "_NEGATION",
    "_NONBOLD_SPLIT",
    "_NOT_AFTER_OF",
    "_OLD_PHASE_SLUG",
    "_OLD_TASK_SLUG",
    "_OPEN_ASIDE",
    "_OPTMEM",
    "_PATH_SUFFIX",
    "_PROSE_DEP",
    "_PROSE_NEGATED",
    "_PROSE_NEGATING",
    "_RESOURCES",
    "_SECTION_CLOSED",
    "_SECTION_HOLD",
    "_SECTION_LIVE",
    "_SENTENCE_END",
    "_STATUS_CLOSED",
    "_STATUS_HOLD",
    "_STATUS_LINE",
    "_STATUS_LIVE",
    "_TITLE_ASIDE",
    "_TITLE_CHARS",
    "_VERDICT_FILLER",
    "_WORD_END",
    "_clean_title",
    "_continuation",
    "_cut_title",
    "_is_id",
    "_leads_with",
    "_marker_in",
    "_prose_globs",
    "_prose_needs",
    "_split_id",
    "_split_nonbold",
    "_title_of",
    "_titled",
    "_verdict_asides",
]
