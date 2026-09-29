"""What the project remembers: lessons, research, bugs, sessions, and the history.

Three refusals here are the reason this family is policy rather than plumbing, and each
exists because the record is worthless without it:

* **A research verdict must be CONFIRMED, REFUTED or THEORETICAL**, and the first two
  need a probe. A verdict with no probe behind it is an opinion, and a note with no
  verdict is a literature summary.
* **A bug may not be closed without naming the regression test** that would catch it
  again — write the test, watch it FAIL against the unfixed code, then close.
* **Recall is a prompt to CHECK, not a verdict.** That sentence ships in the output
  because the failure mode is an agent treating a three-week-old prompt as binding.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import csv_list
from ..core import outcome as O
from ..core.ids import auto_id
from ._base import _load

VERDICTS = ("CONFIRMED", "REFUTED", "THEORETICAL")


def _store(repo, log, cfg):
    from ..infra.store import Store

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


def lesson_add(repo: Path, draft: LessonDraft, *, agent: str = "") -> O.Outcome:
    """Record a transferable rule — the pattern, not the incident.

    With ``pattern``, the repository is SCANNED NOW and the matching sites are stored with
    the lesson (B20). That is the whole mechanism: `ddflow lesson verify` re-scans later
    and names which sites appeared, where a stored count could only say that things got
    worse. A count cannot be acted on and cannot be reviewed.
    """
    from ..services import inventory as INV

    log, _cfg, _st = _load(repo, agent)
    lid = draft.id or auto_id("L", draft.title, draft.rule)
    data: dict[str, Any] = {
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
    log.append("lesson.recorded", lid, data)
    return O.ok("lesson.recorded", id=lid, sites=len(sites), inventory=sites)


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
    from ..services import inventory as INV

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


def recall(
    repo: Path,
    query: str,
    *,
    sources: str = "",
    limit: int = 3,
    max_chars: int = 4000,
    agent: str = "",
) -> O.Outcome:
    """ "Have we been here before?" — one query across everything the project remembers.

    Searches architectural decisions, lessons, research verdicts, past bugs, similar
    tasks and the operator's own earlier prompts, and labels each hit by WHAT KIND of
    thing it is — because "should this change what I do" has a different answer for a
    binding decision, a transferable lesson and a prompt from three weeks ago.

    Exists so an operator does not have to say the same thing twice and an agent does not
    have to learn the same thing twice. Both failures are invisible in the moment and
    obvious in the log.
    """
    from ..infra.store import RECALL_SOURCES, summarise_row

    log, cfg, _st = _load(repo, agent)
    store = _store(repo, log, cfg)
    want = csv_list(sources) or [t for t, _, _ in RECALL_SOURCES]
    lowered = [w.lower() for w in want]
    results: dict[str, list[dict]] = {}
    for table, label, _why in RECALL_SOURCES:
        if table not in want and label.lower() not in lowered:
            continue
        try:
            hits = store.search(table, query, limit)
        except Exception:
            # One unreadable source must not take the whole recall down: the value is in
            # the union, and "the lessons table is corrupt" is not a reason to withhold
            # the decisions.
            hits = []
        if hits:
            results[table] = hits

    labels = {table: label for table, label, _ in RECALL_SOURCES}
    wire = {
        table: [
            {
                "id": r.get("id"),
                "kind": labels[table],
                "headline": summarise_row(table, r)[0],
                "body": summarise_row(table, r)[1],
                "raw": r,
            }
            for r in rows
        ]
        for table, rows in results.items()
    }
    data: dict[str, Any] = {
        "results": wire,
        "query": query,
        "searched": [t for t, _, _ in RECALL_SOURCES],
        "max_chars": max_chars,
        "_render": {"results": results, "sources": RECALL_SOURCES},
    }
    if not results:
        return O.nothing(
            "recall",
            f"Nothing recalled for {query!r}.\n"
            f"Searched: {', '.join(t for t, _, _ in RECALL_SOURCES)}.",
            **data,
        )
    return O.ok("recall", **data)


@dataclass
class Finding:
    """One research result, named once.

    Eleven fields that travel together — argparse flags, MCP input properties, event
    payload. The same reason `decisions.Draft` and `gates.Evidence` exist: a field added
    to one of those three lists is a field the other two silently drop.
    """

    question: str
    verdict: str
    claim: str = ""
    mechanism: str = ""
    falsifier: str = ""
    probe: str = ""
    probe_output: str = ""
    sources: str = ""
    budget: str = ""
    item: str = ""
    id: str = ""


def research_add(repo: Path, finding: Finding, *, agent: str = "") -> O.Outcome:
    """Record a research finding, with the verdict it earned.

    Both refusals are the rule that makes the record worth keeping: a note with no
    verdict is a literature summary, and a CONFIRMED with no probe is an opinion wearing
    a label.
    """
    if finding.verdict not in VERDICTS:
        return O.failed(
            "research.recorded",
            "verdict must be CONFIRMED, REFUTED or THEORETICAL. A note with no verdict "
            "is a literature summary, not research.",
        )
    if finding.verdict in ("CONFIRMED", "REFUTED") and not (finding.probe or finding.probe_output):
        return O.failed(
            "research.recorded",
            f"{finding.verdict} requires a --probe (and ideally --probe-output): a "
            f"verdict with no probe behind it is an opinion. Use THEORETICAL and say why "
            f"no probe was possible.",
        )
    log, _cfg, _st = _load(repo, agent)
    rid = finding.id or auto_id("R", finding.question, finding.claim)
    log.append(
        "research.recorded",
        rid,
        {
            "question": finding.question,
            "claim": finding.claim,
            "mechanism": finding.mechanism,
            "falsifier": finding.falsifier,
            "probe": finding.probe,
            "probe_output": finding.probe_output,
            "verdict": finding.verdict,
            "sources": csv_list(finding.sources),
            "budget": finding.budget,
            "item": finding.item,
        },
    )
    return O.ok("research.recorded", id=rid, verdict=finding.verdict)


def bug_found(
    repo: Path, *, summary: str, item: str = "", id: str = "", agent: str = ""
) -> O.Outcome:
    log, _cfg, _st = _load(repo, agent)
    bid = id or auto_id("B", summary, item)
    log.append("bug.found", bid, {"item": item, "summary": summary})
    return O.ok("bug.found", id=bid)


def bug_fixed(
    repo: Path,
    item: str,
    *,
    regression_test: str = "",
    lesson: str = "",
    lesson_title: str = "",
    lesson_rule: str = "",
    agent: str = "",
) -> O.Outcome:
    """Close a bug. Refuses without the test that would catch it again."""
    log, cfg, st = _load(repo, agent)
    if not regression_test and cfg.lessons.require_regression_test:
        return O.failed(
            "bug.fixed",
            "a bug may not be closed without --regression-test naming the test that "
            "would catch it again. Write the test, watch it FAIL against the unfixed "
            "code, then close.",
            id=item,
        )
    # An unknown id used to be closed anyway, folding a phantom bug while the real one
    # stayed open -- typically the TASK id, passed because `bug found --item` links one.
    if item not in st.bugs:
        linked = sorted(b.id for b in st.bugs.values() if b.item == item and not b.fixed_at)
        hint = (
            f" Open bugs linked to {item}: {', '.join(linked)} -- close those ids."
            if linked
            else " `ddflow recall` or `ddflow status` lists the open bugs."
        )
        return O.refused("bug.fixed", f"no bug {item} is recorded in this log.{hint}", id=item)
    missing, unchecked = _unresolved_tests(repo, regression_test)
    if missing:
        return O.failed(
            "bug.fixed",
            f"--regression-test names a test that exists in no worktree of this "
            f"repository: {', '.join(missing)}. Name the test that now guards this bug.",
            id=item,
        )
    log.append("bug.fixed", item, {"regression_test": regression_test, "lesson": lesson})
    captured = ""
    if cfg.lessons.auto_capture_on_bug and lesson_title:
        captured = f"L-{item}"
        log.append(
            "lesson.recorded",
            captured,
            {
                "title": lesson_title,
                "rule": lesson_rule,
                "seen_in": [item],
                "tags": ["bug"],
            },
        )
    return O.ok(
        "bug.fixed",
        id=item,
        regression_test=regression_test,
        lesson_captured=captured,
        unchecked=unchecked,
    )


def _unresolved_tests(repo: Path, spec: str) -> tuple[list[str], list[str]]:
    """Split a `--regression-test` list into (missing, unchecked) pytest node ids.

    A node id (`path.py::name[...]`, `path.py::Class::name`) is looked up in EVERY
    worktree, because a bug is closed from the fix branch before its test reaches the
    base. Only names are checked, statically: whether the test FAILS without the fix is
    B-bugfix-verified's job. Anything that is not a Python node id -- a spec, a shell
    command -- cannot be resolved here and is returned as unchecked, not refused.
    """
    from ..infra import worktree as W

    trees = [Path(t["worktree"]) for t in W.list_worktrees(repo) if t.get("worktree")] or [repo]
    missing: list[str] = []
    unchecked: list[str] = []
    for entry in _split_outside_brackets(spec):
        path, sep, names = entry.partition("::")
        # A command (`pytest tests/test_x.py`) can end in `.py` too; a path has no
        # whitespace (B65bbe327c7).
        if not path.endswith(".py") or any(c.isspace() for c in path):
            unchecked.append(entry)
            continue
        # The parametrize id is cut off BEFORE splitting: `::` and `,` are legal inside
        # `[...]`, and splitting them refused a real test (B-bfu-param-sep).
        wanted = names.split("[", 1)[0].split("::") if sep else []
        # `path::` or `path::[p]` names no test; an empty part must not pass for one.
        if "" in wanted or not any(_defines(_inside(tree, path), wanted) for tree in trees):
            missing.append(entry)
    return missing, unchecked


def _split_outside_brackets(spec: str) -> list[str]:
    """Comma-separated entries, ignoring commas inside a parametrize id's brackets."""
    out, depth, cur = [], 0, []
    for ch in spec:
        if ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
            continue
        depth += {"[": 1, "]": -1}.get(ch, 0)
        cur.append(ch)
    out.append("".join(cur))
    return [e.strip() for e in out if e.strip()]


def _inside(tree: Path, path: str) -> Path | None:
    """`tree / path`, or None when it resolves outside `tree`.

    An absolute path made `tree / path` discard the tree, and `..` walks out of it, so
    any file anywhere could close a bug (B-bfu-abs-path).
    """
    candidate = (tree / path).resolve()
    return candidate if candidate.is_relative_to(tree.resolve()) else None


def _defines(source: Path | None, names: list[str]) -> bool:
    """Does `source` exist and define the node `names` -- module, then class, then method?

    Resolved as a CHAIN: matching each name anywhere in the file accepted a method for a
    module-level test and a function outside the class for `Class::method`
    (B-bfu-structure). A file that does not parse under THIS interpreter may still be
    valid under the project's own, so it falls back to finding each name defined
    somewhere -- the permissive side, since refusing a real test locks the bug open.
    """
    if source is None:
        return False
    try:
        raw = source.read_bytes()
    except OSError:
        return False
    try:
        # Bytes, so a PEP 263 coding cookie is honoured as pytest would (B1d4b2e6914).
        scope: list[ast.stmt] = ast.parse(raw).body
    except (SyntaxError, ValueError):
        text = raw.decode("utf-8", errors="replace")
        return all(
            re.search(rf"^\s*(?:async\s+def|def|class)\s+{re.escape(n)}\b", text, re.M)
            for n in names
        )
    module = _definitions(scope)
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for depth, name in enumerate(names):
        found = (
            next((d for d in module if d.name == name), None)
            if node is None
            else _member(node, name, module, set())
        )
        if found is None:
            return False
        if found is True:
            return True
        node = found
        if depth < len(names) - 1 and not isinstance(node, ast.ClassDef):
            return False  # pytest collects from classes only, never a function body
    return True


def _member(
    cls: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
    module: list[ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef],
    seen: set[str],
) -> ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef | bool | None:
    """`name` defined in `cls` or, as pytest collects it, inherited from a base.

    Bases are followed when they are classes of the same module (Bd671110650). A base
    defined elsewhere cannot be read here, so its members are unknown: True, the
    permissive side, since refusing a real test locks the bug open.
    """
    own = next((d for d in _definitions(cls.body) if d.name == name), None)
    if own is not None or not isinstance(cls, ast.ClassDef):
        return own
    seen.add(cls.name)
    unknown = False
    for base in cls.bases:
        if isinstance(base, ast.Name) and base.id == "object":
            continue  # defines no tests; treating it as unknown would accept any name
        local = next(
            (
                d
                for d in module
                if isinstance(base, ast.Name) and isinstance(d, ast.ClassDef) and d.name == base.id
            ),
            None,
        )
        if local is None:
            unknown = True
        elif local.name not in seen:
            hit = _member(local, name, module, seen)
            if hit is not None:
                return hit
    return True if unknown else None


def _definitions(
    body: list[ast.stmt],
) -> list[ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef]:
    """Classes and functions defined directly in `body`, including under `if`/`try`/`with`
    at the same level, but not inside another definition."""
    out: list[ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            out.append(node)
        elif isinstance(
            node, ast.If | ast.Try | ast.With | ast.AsyncWith | ast.For | ast.AsyncFor | ast.While
        ):
            for block in (node.body, getattr(node, "orelse", []), getattr(node, "finalbody", [])):
                out += _definitions(block)
            for handler in getattr(node, "handlers", []):
                out += _definitions(handler.body)
        elif isinstance(node, ast.Match):
            for case in node.cases:
                out += _definitions(case.body)
    return out


def session_start(repo: Path, *, model: str = "", tool: str = "", agent: str = "") -> O.Outcome:
    from ..services import sessions as S

    log, cfg, _st = _load(repo, agent)
    return O.ok("session.started", session=S.start(log, cfg, model=model, agent_tool=tool))


#: Why an empty session record is refused rather than written.
_EMPTY_SESSION_TEXT = (
    "refusing an empty {what}: the text is empty or whitespace-only, and nothing was "
    "recorded. Pass the words with --text (or pipe them on stdin)."
)


def session_prompt(
    repo: Path, session: str, text: str, *, item: str = "", agent: str = ""
) -> O.Outcome:
    """Record the operator's own words, with credentials redacted before they touch disk.

    Empty or whitespace-only text is refused, recording nothing: an empty prompt in the
    log is a hole `ddflow replay` cannot see as one.
    """
    from ..services import sessions as S

    if not (text or "").strip():
        return O.failed(
            "session.prompt", _EMPTY_SESSION_TEXT.format(what="prompt"), session=session
        )
    log, cfg, _st = _load(repo, agent)
    return O.ok(
        "session.prompt", redactions=S.prompt(log, cfg, session, text, item=item), session=session
    )


def session_note(
    repo: Path, session: str, text: str, *, item: str = "", agent: str = ""
) -> O.Outcome:
    from ..services import sessions as S

    if not (text or "").strip():
        return O.failed("session.note", _EMPTY_SESSION_TEXT.format(what="note"), session=session)
    log, cfg, _st = _load(repo, agent)
    S.note(log, cfg, session, text, item=item)
    return O.ok("session.note", session=session)


def session_end(repo: Path, session: str, *, summary: str = "", agent: str = "") -> O.Outcome:
    from ..services import sessions as S

    log, _cfg, _st = _load(repo, agent)
    S.end(log, session, summary=summary)
    return O.ok("session.ended", session=session)


def history(
    repo: Path,
    *,
    item: str = "",
    kind: str = "",
    since: str = "",
    limit: int = 40,
    agent: str = "",
) -> O.Outcome:
    """One reverse-chronological timeline of everything that happened.

    Ordered by `(lamport, agent, id)` like everything else — NOT by wall-clock timestamp.
    Two agents on two machines have two clocks, and sorting a merged history by `ts` would
    interleave them wrongly while looking perfectly plausible.
    """
    log, _cfg, _st = _load(repo, agent)
    events = log.read_all()
    if item:
        events = [e for e in events if e.subject == item]
    if kind:
        wanted = set(csv_list(kind))
        events = [e for e in events if e.kind in wanted or e.kind.split(".")[0] in wanted]
    if since:
        events = [e for e in events if e.ts >= since]
    events = sorted(events, key=lambda e: (e.lamport, e.agent, e.id), reverse=True)
    shown = events[:limit]
    data: dict[str, Any] = {
        "total": len(events),
        "shown": len(shown),
        "events": [
            {
                "id": e.id,
                "at": e.ts,
                "lamport": e.lamport,
                "agent": e.agent,
                "kind": e.kind,
                "subject": e.subject,
                "data": e.data,
            }
            for e in shown
        ],
        "_render": {"events": shown, "total": len(events)},
    }
    if not shown:
        return O.nothing("history", "Nothing in the history matches.", **data)
    return O.ok("history", **data)


# -- operational memory ------------------------------------------------------------------


def memory_add(
    repo: Path, text: str, *, tags: str = "", id: str = "", agent: str = ""
) -> O.Outcome:
    """Remember one operational fact about this machine, repository or working state.

    Refused over `[memory] max_chars`, not truncated: a memory cut mid-sentence says
    something its author did not, and the refusal tells them to write a lesson or a
    journal note instead -- which is what a paragraph is.
    """
    log, cfg, st = _load(repo, agent)
    text = " ".join((text or "").split())
    if not text:
        return O.failed("memory.recorded", "a memory needs text", id="")
    limit = cfg.memory.max_chars
    if len(text) > limit:
        return O.failed(
            "memory.recorded",
            f"{len(text)} characters is over [memory] max_chars ({limit}). A memory is ONE "
            f"operational fact; a rule belongs in `lesson add`, what happened in "
            f"`session note`.",
            id="",
        )
    mid = id or auto_id("M", text)
    data: dict[str, Any] = {"text": text}
    if tags:
        # Only when given: the fold MERGES, keeping a field the event omits, and an
        # always-present `tags: []` made correcting a fact by `--id` wipe its tags
        # (cross-family critic).
        data["tags"] = csv_list(tags)
    log.append("memory.recorded", mid, data)
    replaced = bool(id) and id in st.memories
    return O.ok("memory.recorded", id=mid, replaced=replaced)


def _memory_row(m) -> dict[str, Any]:
    return {
        "id": m.id,
        "text": m.text,
        "tags": m.tags,
        "at": m.origin_at or m.at,
        "by": m.by,
        "source": m.source,
        "forgotten": m.forgotten,
    }


def memory_list(
    repo: Path,
    *,
    query: str = "",
    limit: int = 0,
    include_forgotten: bool = False,
    agent: str = "",
) -> O.Outcome:
    """Live memories, newest first; `query` ranks them instead; `include_forgotten` adds
    the ones the project stopped believing.

    Newest first because memory is operational state, and the latest word on "which
    GPUs are free" is the one that matters.
    """
    log, cfg, st = _load(repo, agent)
    if query:
        store = _store(repo, log, cfg)
        # `limit` defaults to ALL here as on the other path; a silent cap of 20 returned
        # a truncated answer presented as complete (roborev 825).
        ids = [r["id"] for r in store.search("memories", query, limit or max(1, len(st.memories)))]
        rows = [st.memories[i] for i in ids if i in st.memories]
        if include_forgotten:
            # The index holds LIVE memories only, so a forgotten one must be matched
            # here -- or `--all` means nothing whenever `--query` is given (roborev 825).
            terms = [t.lower() for t in query.split() if t]
            rows += [
                m
                for m in st.memories.values()
                if not m.live and any(t in m.text.lower() for t in terms)
            ]
    else:
        rows = sorted(
            (m for m in st.memories.values() if include_forgotten or m.live),
            key=lambda m: (m.origin_at or m.at, m.at),
            reverse=True,
        )
        if limit:
            rows = rows[:limit]
    data = {
        "memories": [_memory_row(m) for m in rows],
        "total_live": sum(1 for m in st.memories.values() if m.live),
    }
    if not rows:
        return O.nothing(
            "memory.list", "no memories" + (f" match {query!r}" if query else ""), **data
        )
    return O.ok("memory.list", **data)


def memory_forget(repo: Path, id: str, *, reason: str = "", agent: str = "") -> O.Outcome:
    """Stop believing a memory. It is kept, with the reason -- "we thought X until Y" is
    what stops the next agent re-learning X."""
    log, _cfg, st = _load(repo, agent)
    if not reason.strip():
        return O.failed(
            "memory.forgotten", "--reason is required: why is it no longer true?", id=id
        )
    m = st.memories.get(id)
    if m is None:
        return O.failed("memory.forgotten", f"no such memory {id!r}", id=id)
    if not m.live:
        return O.nothing("memory.forgotten", f"{id} is already forgotten: {m.forgotten}", id=id)
    log.append("memory.forgotten", id, {"reason": reason.strip()})
    return O.ok("memory.forgotten", id=id)
