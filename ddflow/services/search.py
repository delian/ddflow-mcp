"""`ddflow search`: one search across everything the project records (read-only).

Sources (`--source`, registered with the search core): records (task, phase, bug, research,
decision, lesson), sessions (notes and summaries), prompts (what the operator said), log (the
payload text of every other event), rules, skills (skills and commands), agents, jobs and
schedules. `--kind` narrows by the kind of row. Three
modes: ranked (default; the TF-IDF engine `core/textsim.py` that duplicate detection
uses), `exact` (case-insensitive substring) and `regex`.

A hit is a line of facts plus a snippet. The snippet is cut from text that has ALREADY
been redacted, so a secret cannot be left half-covered by the window, and a secret
cannot be searched for (it is not in the text that is matched against the redacted
form either: matching happens on the raw text only to pick candidates, and a candidate
whose match disappears under redaction shows its start instead).

`regex` runs Python's backtracking engine, which cannot be interrupted, so a pattern is
checked before it runs (`check_regex`): too long, a back-reference, a variable-length repeat
inside a repeat (unless together they run at most `MAX_BRANCH_REPS` times), an alternation inside a repeat that can run more than `MAX_BRANCH_REPS`
times, or more than
`MAX_OPEN_REPEATS` unbounded repeats are refused with the reason. What passes is run on
at most `MAX_SCAN` characters per record under an overall time budget.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import Config
from ..core import textsim
from ..core.model import State
from ..core.rank import tfidf
from ..core.textcut import window
from . import schedule as SCH
from . import session_view as SV
from . import skills as SK
from . import viewers as V
from .export.query import ExportError, _cutoff
from .export.safe import redact_text
from .rules import RulesStorage
from .searchcore.hit import FuncSource, register, registered
from .searchcore.hit import Hit as Doc
from .searchcore.regexsafe import (  # noqa: F401 -- re-exported: the old import path
    MAX_BRANCH_REPS,
    MAX_OPEN_REPEATS,
    MAX_PATTERN,
    SearchError,
    check_regex,
)

SOURCES = (
    "task",
    "phase",
    "bug",
    "research",
    "decision",
    "lesson",
    "session",
    "prompt",
    "log",
    "rule",
    "skill",
    "command",
    "agent",
    "job",
    "schedule",
)
_RECORD_SOURCES = frozenset({"task", "phase", "bug", "research", "decision", "lesson"})
_DEF_KINDS = frozenset({"rule", "skill", "agent", "schedule"})  # the definitions with a source
_OWNER = {  # the source that holds each kind that also leaves events in the log
    **dict.fromkeys(_RECORD_SOURCES, "records"),
    "session": "sessions",
    "prompt": "prompts",
    "rule": "rules",
    "skill": "skills",
    "agent": "agents",
    "schedule": "schedules",
    "job": "jobs",
}
MODES = ("ranked", "exact", "regex")
DEFAULT_LIMIT = 20
MAX_LIMIT = 200
SNIPPET = 140
MAX_SCAN = 2000  # characters of one record that a regex looks at
LOG_WEIGHT = 0.5
BUDGET_S = 5.0  # wall-clock budget for the matching of one request


@dataclass
class Result:
    query: str
    mode: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0
    limit: int = DEFAULT_LIMIT
    filters: dict[str, str] = field(default_factory=dict)
    searched: int = 0
    note: str = ""

    @property
    def truncated(self) -> bool:
        return self.total > len(self.rows)


def _join(*parts: Any) -> str:
    return "\n".join(str(p) for p in parts if p)


def _item_docs(st: State, cfg: Config, kinds: set[str]) -> list[Doc]:
    out = []
    for it in st.items.values():
        if it.removed or it.kind not in kinds:
            continue
        r = V._item_row(st, it, cfg)
        text = _join(it.title, it.body, " ".join(it.tags))
        out.append(Doc(it.kind, it.id, it.state, r["updated"], r["owner"], r["phase"], text))
    return out


def _record_docs(st: State, cfg: Config, kinds: set[str]) -> list[Doc]:
    out = []
    for b in st.bugs.values() if "bug" in kinds else ():
        r = V._bug_row(b, cfg, st)
        text = _join(b.title, b.summary, b.evidence, b.invalid_reason, b.lesson)
        out.append(Doc("bug", b.id, r["state"], r["updated"], "", r["phase"], text))
    for n in st.research.values() if "research" in kinds else ():
        r = V._research_row(n, cfg, st)
        text = _join(
            n.question, n.claim, n.mechanism, n.falsifier, n.probe, n.probe_output, n.verdict
        )
        out.append(Doc("research", n.id, r["state"], r["updated"], "", r["phase"], text))
    for d in st.decisions.values() if "decision" in kinds else ():
        text = _join(d.title, d.context, d.decision, d.consequences, d.alternatives)
        out.append(Doc("decision", d.id, d.status, d.at, d.by, "", text))
    for ls in st.lessons.values() if "lesson" in kinds else ():
        state = "superseded" if ls.superseded_by else "active"
        text = _join(ls.title, ls.rule, ls.why, ls.how, ls.summary)
        out.append(Doc("lesson", ls.id, state, ls.at, ls.by, "", text))
    return out


def _session_docs(events: list, kinds: set[str]) -> list[Doc]:
    out = []
    for s in SV._gather(events).values():
        state = "ended" if s.ended else "open"
        if "session" in kinds and s.summary:
            out.append(Doc("session", s.id, state, s.ended, s.agent, "", s.summary))
        for e in s.entries:
            kind = "prompt" if e["kind"] == "prompt" else "session"
            if kind in kinds and e["text"]:
                out.append(Doc(kind, s.id, state, e["at"], s.agent, "", e["text"]))
    return out


def _log_docs(events: list, kinds: set[str], names: frozenset[str] = frozenset()) -> list[Doc]:
    """The log rows. `names` is the selected sources (empty: all): a family's events are left
    out only when the source that holds them is searched too."""

    def held(kind: str) -> bool:
        return kind in kinds and (not names or _OWNER[kind] in names)

    out = []
    for ev in events:
        head = ev.kind.split(".")[0]
        if head == "session":
            if held("prompt" if ev.kind == "session.prompt" else "session"):
                continue  # the sessions and prompts sources hold what these events wrote
        elif head in _RECORD_SOURCES:
            if held(head):
                continue  # a record source already holds what this event wrote
        elif head in ("job", "schedule"):
            if held(head):
                continue  # the jobs and schedules sources do too
        elif head == "def":
            kind = ev.subject.partition(":")[0]
            if kind in _DEF_KINDS and held(kind):
                continue  # and the rules, skills, agents and schedules ones, which are definitions
        payload = json.dumps(ev.data or {}, ensure_ascii=False, sort_keys=True)
        out.append(
            Doc("log", ev.id, ev.kind, ev.ts, ev.agent, "", f"{ev.kind} {ev.subject} {payload}")
        )
    return out


def _def_docs(st: State, def_kind: str, kind: str) -> list[Doc]:
    """The recorded definitions (`State.defs`) of one family as rows of `kind`."""
    out = []
    for rec in st.defs.values():
        if rec.kind != def_kind:
            continue
        vals = [
            " ".join(map(str, v)) if isinstance(v, list | tuple) else v for v in rec.fields.values()
        ]
        text = _join(
            rec.id,
            *(v for v in vals if isinstance(v, str | int | float)),
            rec.reason,
            rec.successor,
        )
        out.append(Doc(kind, rec.id, rec.status, rec.updated_at or rec.at, rec.by, "", text))
    return out


def _rule_docs(c: Ctx, kinds: set[str]) -> list[Doc]:
    out = _def_docs(c.st, "rule", "rule")
    if c.repo is not None:  # a file not (yet) recorded as a definition
        have = {d.id for d in out}
        for r in RulesStorage(c.repo).list():
            if r.id in have:
                continue
            text = _join(r.id, r.title, r.content, " ".join(r.tags))
            # A rule read from its file carries the stamp as the string it was written as.
            stamp = r.updated.isoformat() if isinstance(r.updated, datetime) else str(r.updated)
            out.append(Doc("rule", r.id, "active", stamp, "", "", text))
        # the instruction files other tools read (.cursor/rules, .kilo, CLAUDE.md, AGENTS.md)
        out += [
            Doc("rule", e.path, "active", "", "", "", _join(e.path, e.text))
            for e in SK.inventory(c.repo)
            if e.kind == "rule"
        ]
    return out


def _skill_docs(c: Ctx, kinds: set[str]) -> list[Doc]:
    out = [d for d in _def_docs(c.st, "skill", "skill") if "skill" in kinds]
    if c.repo is not None:
        have = {d.id for d in out}
        for e in SK.inventory(c.repo):
            if e.kind in kinds and e.kind in ("skill", "command") and e.name not in have:
                out.append(Doc(e.kind, e.name, "active", "", "", "", _join(e.name, e.text)))
    return out


def _agent_docs(c: Ctx, kinds: set[str]) -> list[Doc]:
    out = _def_docs(c.st, "agent", "agent")
    have = {d.id for d in out}
    if c.repo is not None:
        for p in sorted((c.repo / ".claude" / "agents").glob("*.md")):
            try:
                body = p.read_text("utf-8", errors="replace")
            except OSError:
                continue
            if body.strip() and p.stem not in have:
                out.append(Doc("agent", p.stem, "active", "", "", "", _join(p.stem, body[:4000])))
    return out


def _job_docs(c: Ctx, kinds: set[str]) -> list[Doc]:
    return [
        Doc(
            "job",
            j.id,
            "ended" if j.ended_at else "started",
            j.ended_at or j.started_at,
            j.by,
            "",
            _join(j.id, j.item, j.command, j.note),
        )
        for j in c.st.jobs.values()
    ]


def _schedule_docs(c: Ctx, kinds: set[str]) -> list[Doc]:
    out = _def_docs(c.st, "schedule", "schedule")
    have = {d.id for d in out}
    if c.repo is not None:
        for jid, d in sorted(SCH.definitions(c.repo, c.cfg, c.st).jobs.items()):
            if d.source == SCH.SOURCE_CADENCE or jid in have:
                continue  # the built-in [cadence] passes are config defaults, not definitions
            j = d.job
            text = _join(
                jid,
                j.title,
                j.prompt,
                j.concurrency_group,
                j.mode,
                *j.tags,
                *j.needs,
                *j.scope_globs,
            )
            out.append(
                Doc("schedule", jid, "enabled" if j.enabled else "disabled", j.at, j.by, "", text)
            )
    return out


@dataclass(frozen=True)
class Ctx:
    """What the sources of one request read: the folded state, the events, the config and,
    for the sources that read files, the repository (None: those sources yield nothing)."""

    st: State
    events: list
    cfg: Config
    repo: Path | None = None
    names: frozenset[str] = frozenset()  # the sources asked for; empty: all


# The sources, in the order their rows are listed before ranking.
register(
    FuncSource(
        "records",
        tuple(sorted(_RECORD_SOURCES)),
        lambda c, kinds: _item_docs(c.st, c.cfg, kinds) + _record_docs(c.st, c.cfg, kinds),
    )
)
register(
    FuncSource(
        "sessions", ("session",), lambda c, kinds: _session_docs(c.events, kinds & {"session"})
    )
)
register(
    FuncSource("prompts", ("prompt",), lambda c, kinds: _session_docs(c.events, kinds & {"prompt"}))
)
register(FuncSource("log", ("log",), lambda c, kinds: _log_docs(c.events, kinds, c.names)))
register(FuncSource("rules", ("rule",), _rule_docs))
register(FuncSource("skills", ("skill", "command"), _skill_docs))
register(FuncSource("agents", ("agent",), _agent_docs))
register(FuncSource("jobs", ("job",), _job_docs))
register(FuncSource("schedules", ("schedule",), _schedule_docs))


def source_names() -> list[str]:
    """The names `--source` takes: every registered source."""
    return [s.name for s in registered()]


def _docs(
    st: State, events: list, cfg: Config, kinds: set[str], repo: Path | None, names: set[str]
) -> list[Doc]:
    ctx = Ctx(st, events, cfg, repo, frozenset(names))
    out: list[Doc] = []
    for src in registered():
        if (not names or src.name in names) and kinds & set(src.kinds):
            out += src.hits(ctx, kinds)
    return out


# ---------------------------------------------------------------- snippets


def _snippet(flat: str, span: tuple[int, int] | None) -> str:
    """A window of the whitespace-folded text around `span` (or its start)."""
    if span is None:
        return window(flat, 0, SNIPPET)
    width = span[1] - span[0]
    return window(flat, max(0, span[0] - max(0, SNIPPET - width) // 2), SNIPPET)


def _term_span(text: str, words: list[str]) -> tuple[int, int] | None:
    low = text.lower()
    best = None
    for w in words:
        i = low.find(w)
        if i >= 0 and (best is None or i < best[0]):
            best = (i, i + len(w))
    return best


# ---------------------------------------------------------------- the search


@dataclass
class Filters:
    kinds: str = ""
    state: str = ""
    phase: str = ""
    agent: str = ""
    since: str = ""
    source: str = ""

    def given(self) -> dict[str, str]:
        mine = {
            "kind": self.kinds,
            "state": self.state,
            "phase": self.phase,
            "agent": self.agent,
            "since": self.since,
            "source": self.source,
        }
        return {k: v for k, v in mine.items() if v}


def _source_names(f: Filters) -> set[str]:
    """The sources asked for (empty: every one); an unknown name is refused."""
    wanted = {k.strip().lower() for k in f.source.split(",") if k.strip()}
    known = source_names()
    bad = sorted(wanted - set(known))
    if bad:
        raise SearchError(f"unknown source {', '.join(bad)}: one of {', '.join(known)}")
    return wanted


def _sources(f: Filters) -> set[str]:
    wanted = [k.strip().lower() for k in f.kinds.split(",") if k.strip()]
    bad = [k for k in wanted if k not in SOURCES]
    if bad:
        raise SearchError(f"unknown kind {', '.join(bad)}: one of {', '.join(SOURCES)}")
    return set(wanted) or set(SOURCES)


def _narrow(docs: list[Doc], st: State, f: Filters) -> list[Doc]:
    if f.phase and not (f.phase in st.items and st.items[f.phase].kind == "phase"):
        raise SearchError(f"no phase {f.phase!r}")
    try:
        at_or_after = _cutoff(f.since) if f.since else None
    except ExportError as exc:
        raise SearchError(str(exc).replace("--since", "since")) from None
    if f.state:
        docs = [d for d in docs if d.state.lower() == f.state.lower()]
    if f.phase:
        docs = [d for d in docs if d.phase == f.phase]
    if f.agent:
        docs = [d for d in docs if d.owner == f.agent]
    if at_or_after is not None:
        docs = [d for d in docs if d.date and at_or_after(d.date)]
    return docs


def _ranked(docs: list[Doc], query: str) -> list[tuple[float, Doc]]:
    qtoks = textsim.tokens(query)
    if not qtoks:
        raise SearchError(
            "nothing searchable in that text (only stop words or ids); "
            "use --exact for a literal match"
        )
    scores = tfidf([textsim.tokens(d.text[: 2 * MAX_SCAN]) for d in docs], qtoks)
    # The log repeats what the records say, in terse machine words; rank it last.
    return [(sc * LOG_WEIGHT if docs[i].kind == "log" else sc, docs[i]) for i, sc in scores.items()]


def _scan(
    docs: list[Doc], query: str, rx: re.Pattern[str] | None, res: Result
) -> list[tuple[float, Doc]]:
    deadline = time.monotonic() + BUDGET_S
    needle = query.lower()
    hits = []
    scanned = 0
    for d in docs:
        if time.monotonic() > deadline:
            res.note = f"stopped after {BUDGET_S:g}s; the results are partial"
            res.searched = scanned
            break
        scanned += 1
        found = rx.search(d.text[:MAX_SCAN]) is not None if rx else needle in d.text.lower()
        if found:
            hits.append((0.0, d))
    return hits


def _span(flat: str, query: str, mode: str, rx: re.Pattern[str] | None) -> tuple[int, int] | None:
    if rx is not None:
        m = rx.search(flat[:MAX_SCAN])
        return (m.start(), m.end()) if m else None
    if mode == "exact":
        norm = " ".join(query.lower().split())
        i = flat.lower().find(norm)
        return (i, i + len(norm)) if i >= 0 else None
    words = sorted({w for w in re.findall(r"\w+", query.lower()) if len(w) > 1}, key=len)
    return _term_span(flat, words)


def search(
    st: State,
    events: list,
    cfg: Config,
    query: str,
    filters: Filters | None = None,
    *,
    mode: str = "ranked",
    limit: int = DEFAULT_LIMIT,
    repo: Path | None = None,
) -> Result:
    f = filters or Filters()
    if mode not in MODES:
        raise SearchError(f"unknown mode {mode!r}: one of {', '.join(MODES)}")
    if not (query or "").strip():
        raise SearchError("nothing to search for: give some text")
    if limit < 1:
        raise SearchError(f"limit must be at least 1, got {limit}")
    limit = min(limit, MAX_LIMIT)
    kinds = _sources(f)
    names = _source_names(f)
    rx = check_regex(query) if mode == "regex" else None
    docs = _narrow(_docs(st, events, cfg, kinds, repo, names), st, f)
    res = Result(
        query=redact_text(query, cfg).text,
        mode=mode,
        limit=limit,
        filters=f.given(),
        searched=len(docs),
    )
    hits = _ranked(docs, query) if mode == "ranked" else _scan(docs, query, rx, res)
    # Best score first; then newest; then id, so a run is repeatable.
    hits.sort(key=lambda h: h[1].id)
    hits.sort(key=lambda h: h[1].date, reverse=True)
    hits.sort(key=lambda h: h[0], reverse=True)
    res.total = len(hits)
    for score, d in hits[:limit]:
        flat = " ".join(redact_text(d.text, cfg).text.split())
        res.rows.append(
            {
                "kind": d.kind,
                "id": redact_text(d.id, cfg).text,
                "state": d.state,
                "date": d.date,
                "owner": d.owner,
                "snippet": _snippet(flat, _span(flat, query, mode, rx)),
                "score": round(score, 4) if mode == "ranked" else None,
            }
        )
    return res
