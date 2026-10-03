"""`ddflow search`: one search across everything the project records (read-only).

Sources: task, phase, bug, research, decision, lesson, session (notes and summaries),
prompt (what the operator said) and log (the payload text of every other event). Three
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
from typing import Any

from ..config import Config
from ..core import textsim
from ..core.model import State
from . import session_view as SV
from . import viewers as V
from .export.query import ExportError, _cutoff
from .export.safe import redact_text

SOURCES = ("task", "phase", "bug", "research", "decision", "lesson", "session", "prompt", "log")
_RECORD_SOURCES = frozenset({"task", "phase", "bug", "research", "decision", "lesson"})
MODES = ("ranked", "exact", "regex")
DEFAULT_LIMIT = 20
MAX_LIMIT = 200
SNIPPET = 140
MAX_SCAN = 2000  # characters of one record that a regex looks at
MAX_PATTERN = 200
MAX_OPEN_REPEATS = 2
MAX_BRANCH_REPS = 8  # an alternation may sit inside a repeat that runs at most this often
LOG_WEIGHT = 0.5
BUDGET_S = 5.0  # wall-clock budget for the matching of one request


class SearchError(ValueError):
    """The request cannot be answered as asked; the message says what to change."""


@dataclass
class Doc:
    kind: str
    id: str
    state: str
    date: str
    owner: str
    phase: str
    text: str


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


def _log_docs(events: list, kinds: set[str]) -> list[Doc]:
    out = []
    for ev in events:
        head = ev.kind.split(".")[0]
        if head == "session" or head in kinds & _RECORD_SOURCES:
            continue  # a record source already holds what this event wrote
        payload = json.dumps(ev.data or {}, ensure_ascii=False, sort_keys=True)
        out.append(
            Doc("log", ev.id, ev.kind, ev.ts, ev.agent, "", f"{ev.kind} {ev.subject} {payload}")
        )
    return out


def _docs(st: State, events: list, cfg: Config, kinds: set[str]) -> list[Doc]:
    out = _item_docs(st, cfg, kinds) + _record_docs(st, cfg, kinds)
    if kinds & {"session", "prompt"}:
        out += _session_docs(events, kinds)
    if "log" in kinds:
        out += _log_docs(events, kinds)
    return out


# ---------------------------------------------------------------- regex safety


def _walk(sre_parse, nodes, reps: int, stats: dict[str, int]) -> None:
    """Refuse what makes the backtracking engine exponential.

    `reps` is how often the enclosing repeats can run the node: 1 outside any repeat, the
    product of their upper bounds, and `MAXREPEAT` once one is unbounded."""
    c = sre_parse
    for op, av in nodes:
        name = str(op)
        if name in ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"):
            lo, hi, sub = av
            unbounded = hi >= c.MAXREPEAT
            if reps > 1 and lo != hi and (unbounded or reps * hi > MAX_BRANCH_REPS):
                raise SearchError(
                    "regex refused: a repeat of variable length inside another repeat "
                    "can take exponential time"
                )
            if unbounded:
                stats["open"] += 1
            _walk(c, sub, c.MAXREPEAT if unbounded else min(reps * hi, c.MAXREPEAT), stats)
        elif name == "BRANCH":
            if reps > MAX_BRANCH_REPS:
                raise SearchError(
                    "regex refused: an alternation inside a repeat can take exponential "
                    "time (use a character class, or repeat at most "
                    f"{MAX_BRANCH_REPS} times)"
                )
            for alt in av[1]:
                _walk(c, alt, reps, stats)
        elif name in ("GROUPREF", "GROUPREF_EXISTS"):
            raise SearchError("regex refused: back-references can take exponential time")
        elif name == "SUBPATTERN":
            _walk(c, av[3], reps, stats)
        elif name in ("ASSERT", "ASSERT_NOT"):
            _walk(c, av[1], reps, stats)
        elif name == "ATOMIC_GROUP":
            _walk(c, av, reps, stats)


def check_regex(pattern: str, *, ignore_case: bool = True) -> re.Pattern[str]:
    """Compile `pattern` if it is safe to run; otherwise raise SearchError saying why."""
    if len(pattern) > MAX_PATTERN:
        raise SearchError(f"regex refused: longer than {MAX_PATTERN} characters")
    try:
        from re import _parser as sre_parse  # type: ignore[attr-defined]
    except ImportError:  # pragma: no cover - Python < 3.11
        import sre_parse  # type: ignore[no-redef]
    try:
        tree = sre_parse.parse(pattern)
        rx = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except (re.error, RecursionError, OverflowError) as exc:
        raise SearchError(f"regex does not compile: {exc}") from None
    stats = {"open": 0}
    _walk(sre_parse, tree, 1, stats)
    if stats["open"] > MAX_OPEN_REPEATS:
        raise SearchError(
            f"regex refused: {stats['open']} unbounded repeats (at most {MAX_OPEN_REPEATS})"
        )
    return rx


# ---------------------------------------------------------------- snippets


def _snippet(flat: str, span: tuple[int, int] | None) -> str:
    """A window of the whitespace-folded text around `span` (or its start)."""
    if span is None:
        return flat[:SNIPPET] + ("…" if len(flat) > SNIPPET else "")
    width = span[1] - span[0]
    start = max(0, span[0] - max(0, SNIPPET - width) // 2)
    return (
        ("…" if start else "")
        + flat[start : start + SNIPPET]
        + ("…" if start + SNIPPET < len(flat) else "")
    )


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

    def given(self) -> dict[str, str]:
        mine = {
            "kind": self.kinds,
            "state": self.state,
            "phase": self.phase,
            "agent": self.agent,
            "since": self.since,
        }
        return {k: v for k, v in mine.items() if v}


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
    corpus = [textsim.tokens(d.text[: 2 * MAX_SCAN]) for d in docs]
    df, post = textsim.invert(corpus)
    scores = textsim.cosine(textsim.vector(qtoks, df, max(1, len(corpus))), post)
    # The log repeats what the records say, in terse machine words; rank it last.
    return [
        (sc * LOG_WEIGHT if docs[i].kind == "log" else sc, docs[i])
        for i, sc in scores.items()
        if sc > 0
    ]


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
    rx = check_regex(query) if mode == "regex" else None
    docs = _narrow(_docs(st, events, cfg, kinds), st, f)
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
