"""Declared versus written files (LK-observed-globs).

What a task wrote is read by the one reader every completion check uses
(`services.completion.changed_paths`: landed range, worktree or branch); this module only
compares it with the lease globs and words the result. Pure comparisons are separate from the
git read so they are tested without a repository.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Callable
from pathlib import Path

from ..core import globs
from ..core.admission import shared_globs
from ..core.records.state import State
from .completion._changed import changed_paths

#: Returned by `undeclared_report` when git cannot say what changed: the check could not
#: run, which is neither a pass nor a block.
UNKNOWN = (
    "could not tell what this task changed (git could not list it): the undeclared-write "
    "check did not run. Repair the worktree, then re-run `ddflow gate status {id}` "
    "(MCP: ddflow_gate action=status id={id})"
)


def unknown(item: str) -> str:
    """`UNKNOWN` naming ``item`` in the exact call."""
    return UNKNOWN.format(id=item)


#: ddflow's own bookkeeping commits are never the item's to declare.
_OWN = (".ddflow/**",)


def _read(read: Callable | None) -> Callable:
    """The one changed-files reader (`services.completion.changed_paths`)."""
    return read if read is not None else changed_paths


def verdict_notice(
    state: State, cfg, item_id: str, repo: Path, read: Callable | None = None
) -> tuple[str, bool]:
    """(notice, whether it blocks `complete`): blocking only for a known undeclared write
    under ``undeclared_write = block``; a check that could not run only warns."""
    text = undeclared_report(state, cfg, item_id, repo=repo, read=read)
    return text, bool(text) and text != unknown(item_id) and cfg.locks.undeclared_write == "block"


def split(
    paths: list[str], declared: list[str], *, shared: list[str] | None = None
) -> tuple[list[str], list[str]]:
    """(undeclared paths, declared globs that matched no path). A path is declared when it
    is inside one of ``declared``; ddflow's own ``.ddflow/**`` files, and every shared glob
    (`[lease].shared_globs`, ``append_only_globs``, export targets -- regenerated pins the
    brief already says are never leased), are never the task's to declare (B38c3ac0f60)."""
    mine = [p for p in paths if not any(globs.inside(p, g) for g in _OWN)]
    covered = [*declared, *(shared or [])]
    undeclared = [p for p in mine if not any(globs.inside(p, g) for g in covered)]
    untouched = [g for g in declared if not any(globs.inside(p, g) for p in mine)]
    return undeclared, untouched


def directory_globs(paths: list[str]) -> list[str]:
    """The narrowest directory globs covering ``paths`` (``a/b/c.py`` -> ``a/b/*``, a root
    file as itself): what an agent would declare, never a repo-wide glob."""
    out: list[str] = []
    for p in sorted(set(paths)):
        head, _, _ = p.rpartition("/")
        g = f"{head}/*" if head else p
        if g not in out:
            out.append(g)
    return out


def update_call(item: str, declared: list[str], extra: list[str]) -> str:
    """The exact call that declares ``extra`` besides ``declared``, CLI and MCP forms."""
    every = [*declared, *extra]
    # A comma inside a glob would be split by the comma form: the JSON list form is exact.
    joined = json.dumps(every) if any("," in g for g in every) else ",".join(every)
    return (
        f"`ddflow update {item} --globs {shlex.quote(joined)}` "
        f"(MCP: ddflow_task action=update id={item} globs={shlex.quote(joined)})"
    )


def _list(items: list[str], shown: int) -> str:
    more = f" (+{len(items) - shown} more)" if len(items) > shown else ""
    return ", ".join(items[:shown]) + more


def undeclared_report(
    state: State, cfg, item_id: str, *, repo: Path, read: Callable | None = None
) -> str:
    """The notice naming files the task wrote outside its globs and the call that declares
    them; "" when clean, the mode is `off`, or the item is not an open task;
    `UNKNOWN` when git cannot say (a warning, never a blocker)."""
    it = state.items.get(item_id)
    if cfg.locks.undeclared_write == "off" or it is None or it.removed or it.kind != "task":
        return ""
    changed = _read(read)(repo, it)
    if changed is None:
        return unknown(item_id)
    undeclared, _ = split(changed, list(it.globs or []), shared=shared_globs(cfg))
    if not undeclared:
        return ""
    shown = cfg.locks.suggest_shown
    return (
        f"{len(undeclared)} file(s) written outside the declared globs: "
        f"{_list(undeclared, shown)}. Declare them so other agents see the overlap: "
        f"{update_call(item_id, list(it.globs or []), directory_globs(undeclared))}."
    )


def untouched_report(
    state: State, cfg, item_id: str, *, repo: Path, read: Callable | None = None
) -> str:
    """Declared globs the diff never touched, offered for narrowing when the task is ready
    to merge; "" when `suggest_globs` is off, git cannot say, or every glob was used."""
    it = state.items.get(item_id)
    if not cfg.locks.suggest_globs or it is None or it.removed or it.kind != "task":
        return ""
    changed = _read(read)(repo, it)
    if not changed or not it.globs or all(globs.inside(p, ".ddflow/**") for p in changed):
        return ""  # unknown or nothing changed yet: narrowing is not yet knowable
    _, untouched = split(changed, list(it.globs or []), shared=shared_globs(cfg))
    if not untouched:
        return ""
    keep = [g for g in it.globs if g not in untouched]
    wrote = [] if keep else directory_globs(split(changed, [], shared=shared_globs(cfg))[0])
    if not keep and not wrote:
        # Every non-.ddflow write was a shared pin and every declared glob was untouched:
        # there is nothing the task can usefully declare or narrow (B38c3ac0f60).
        return ""
    if wrote:  # nothing was written inside any glob: replace the declaration by what was
        return (
            f"declared but never written: {_list(untouched, cfg.locks.suggest_shown)}; "
            f"this task wrote {_list(wrote, cfg.locks.suggest_shown)} instead. "
            f"Re-declare it so others can take those files: {update_call(item_id, [], wrote)}."
        )
    return (
        f"declared but never written: {_list(untouched, cfg.locks.suggest_shown)}. "
        f"Narrow the claim so others can take those files: "
        f"{update_call(item_id, keep, [])}."
    )


def _habits(state: State, cfg, it, repo: Path, read) -> dict[str, int]:
    """How many finished same-tag tasks (the latest ``suggest_history``) each directory
    glob was written in outside that task's own declaration."""
    mine = set(it.tags)
    past = [
        o
        for o in state.items.values()
        if o.id != it.id and o.state == "done" and o.kind == "task" and mine & set(o.tags or ())
    ]
    past.sort(key=lambda o: o.completed_at or o.id, reverse=True)
    counts: dict[str, int] = {}
    for o in past[: cfg.locks.suggest_history]:
        changed = _read(read)(repo, o)
        undeclared = (
            split(changed, list(o.globs or []), shared=shared_globs(cfg))[0]
            if changed is not None
            else []
        )
        for g in directory_globs(undeclared):
            counts[g] = counts.get(g, 0) + 1
    return counts


def learned(
    state: State, cfg, item_id: str, *, repo: Path, read: Callable | None = None
) -> list[str]:
    """Directory globs that finished tasks sharing a tag with ``item_id`` keep writing
    outside their own declarations, needed in at least ``suggest_min_items`` of them and
    not already declared here."""
    it = state.items.get(item_id)
    if not cfg.locks.suggest_globs or it is None or not it.tags or not cfg.locks.suggest_history:
        return []
    counts = _habits(state, cfg, it, repo, read)
    own = it.globs or []
    chosen = [
        g
        for g, n in counts.items()
        if n >= cfg.locks.suggest_min_items and not any(globs.inside(g, d) for d in own)
    ]
    return sorted(chosen, key=lambda g: (-counts[g], g))


def claim_fields(state: State, cfg, item_id: str, repo: Path) -> dict[str, str]:
    """The extra `claim` output: the learned suggestion, when there is one."""
    hint = learned_report(state, cfg, item_id, repo=repo)
    return {"glob_suggestion": hint} if hint else {}


def add_notice(
    verdict, state: State, cfg, item_id: str, repo: Path, read: Callable | None = None
) -> None:
    """Put the undeclared-write notice on ``verdict`` as a blocker or a warning."""
    text, blocks = verdict_notice(state, cfg, item_id, repo, read=read)
    if text:
        (verdict.blockers if blocks else verdict.warnings).append(text)


def learned_report(
    state: State, cfg, item_id: str, *, repo: Path, read: Callable | None = None
) -> str:
    """The learned suggestions as one line naming the call; "" when there are none."""
    found = learned(state, cfg, item_id, repo=repo, read=read)
    if not found:
        return ""
    it = state.items[item_id]
    return (
        f"finished tasks with the same tags keep also writing: "
        f"{_list(found, cfg.locks.suggest_shown)}. If this one will too: "
        f"{update_call(item_id, list(it.globs or []), found[: cfg.locks.suggest_shown])}."
    )
