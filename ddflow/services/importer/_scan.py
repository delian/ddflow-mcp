"""Lessons, decisions, research, journal, optmem and branch scans."""
from __future__ import annotations

import re
from ...core.schedule import is_external
from ...core.slug import slug as _slug
from ...infra import git as G
from ...infra import worktree as W
from ._titles import DECISION_GLOBS, JOURNAL_GLOBS, LESSON_GLOBS, LESSON_SUMMARY_GLOBS, OPTMEM_GLOBS, RESEARCH_GLOBS
from ._todo import _LESSON_ID

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
                    title=_cut_title(shown),
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
        title=_cut_title((lead.group(1) if lead else text).rstrip(". ")),
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
                title=_cut_title(title, 140),
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
                    title=_cut_title(text),
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

    base = W.default_branch(repo)
    r = G.run(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/", timeout=60)
    if not r.ok:
        return []
    out: list[Found] = []
    for branch in [b.strip() for b in r.out.splitlines() if b.strip()]:
        if branch == base:
            continue
        c = G.run(repo, "rev-list", "--count", f"{base}..{branch}", timeout=60)
        ahead = int(c.out or 0) if c.ok else 0
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

__all__ = ["scan_lessons", "_BULLET", "_CITES", "_TRAILING_CITE", "_GENERATED_MARK", "_summary_bullet", "scan_lesson_summaries", "_LEAD", "_summary_text", "_norm", "_id_key", "_attach_summaries", "scan_decisions", "scan_research", "_RESEARCH_HEAD", "_research_ident", "_research_legacy", "_verdict", "scan_journal", "scan_optmem", "scan_branches", "_NOTE_EXAMPLES", "_flag_shipped_phases_with_open_tasks", "_flag_unresolvable_needs", "_known_ids", "_pull_in_needed"]
