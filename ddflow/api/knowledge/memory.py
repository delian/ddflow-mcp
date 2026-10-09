"""Operational memory: facts about this machine and repository."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import ddflow.api._dedupe as DD

from ...config import csv_list
from ...core import ids as IDS
from ...core import outcome as O
from ...services import searchcore as SC
from .._base import _load
from .lessons import _store

# -- operational memory ------------------------------------------------------------------


def memory_add(
    repo: Path,
    text: str,
    *,
    tags: str = "",
    id: str = "",
    answer: DD.Answer | None = None,
    agent: str = "",
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
    minted = IDS.mint(cfg, st, "memory", events=log.read_all, given=id, hash_parts=(text,))
    mid = minted.id
    chk = DD.check_add(
        repo,
        log,
        cfg,
        st,
        DD.Record(kind="memory", event_kind="memory.recorded", rid=mid, body=text),
        answer,
    )
    if chk.refusal is not None:
        return chk.refusal
    if chk.extension:
        return DD.extend(log, cfg, chk, "memory.recorded")
    data: dict[str, Any] = {"text": text, **IDS.key_field(minted), **chk.fields}
    if tags:
        # Only when given: the fold MERGES, keeping a field the event omits, and an
        # always-present `tags: []` made correcting a fact by `--id` wipe its tags
        # (cross-family critic).
        data["tags"] = csv_list(tags)
    with log.transaction():
        minted = IDS.confirm(cfg, "memory", minted, used=IDS.used_now(log), hash_parts=(text,))
        log.append("memory.recorded", mid, data | IDS.key_field(minted))
        DD.after_add(log, cfg, mid, chk)
    replaced = bool(id) and id in st.memories
    return O.ok("memory.recorded", id=mid, replaced=replaced, **chk.data())


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


def _matching(repo: Path, log, cfg, st, query: str, limit: int, include_forgotten: bool) -> list:
    """The memories `query` ranks, best first (the forgotten ones matching it appended)."""
    store = _store(repo, log, cfg)
    # `limit` defaults to ALL here as on the other path; a silent cap of 20 returned
    # a truncated answer presented as complete (roborev 825).
    hits = SC.search_table(store, "memories", query, limit or max(1, len(st.memories)))
    ids = [r["id"] for r in hits]
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
    return rows


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
        rows = _matching(repo, log, cfg, st, query, limit, include_forgotten)
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
