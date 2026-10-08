"""Filing a bug and the fix task that carries it through the queue."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import ddflow.api._dedupe as DD

from ...core import globspec as GS
from ...core import ids as IDS
from ...core import outcome as O
from ...core.model import fold
from .._base import _load

BUG_SCOPES = ("project", "ddflow")
BUG_SEVERITIES = ("low", "medium", "high", "critical")
#: The command line (with `{id}`) that prepares an upstream report, once there is one ("" until).
#: The offer to prepare a report names it, so it is only made when this is set
#: (tests/test_bug_scope.py pins it to the parser).
BUG_REPORT_COMMAND = ""


def upstream_offer(scope: str, bid: str) -> str:
    """One line offering an upstream report for a ddflow-scoped bug; '' when the bug is
    the project's own or the command that prepares the report is not there yet."""
    if scope != "ddflow" or not BUG_REPORT_COMMAND:
        return ""
    return (
        f"This bug is in ddflow itself: `{BUG_REPORT_COMMAND.format(id=bid)}` "
        "prepares an upstream report."
    )


def bug_found(  # noqa: PLR0913 -- BACKLOG B179: a BugDraft record, as task_add's TaskDraft
    repo: Path,
    *,
    summary: str,
    item: str = "",
    id: str = "",
    title: str = "",
    severity: str = "",
    scope: str = "",
    globs: str = "",
    no_task: bool = False,
    answer: DD.Answer | None = None,
    agent: str = "",
) -> O.Outcome:
    """File a bug. One that reads like an existing record is refused until ``answer``
    says what it is; a bug a task will fix is filed against it (``item``), which answers
    that candidate. ``answer`` extending an OPEN bug appends to it and files nothing.
    ``title``, ``severity`` (low|medium|high|critical) and ``scope`` (``project``, or
    ``ddflow`` for a bug in ddflow itself) are optional event fields.

    A bug is an item in the queue (B-bugs-as-items): unless ``no_task`` (a bug fixed in
    the commit that found it) or ``[bugs].file_task`` is off, the same transaction files
    its fix task -- `_file_fix_task` -- and the reply carries ``fix_task``. ``globs`` are
    the fix task's files; absent, the item's. An OPEN bug-fix task named as ``item`` is
    the fix itself and nothing new is filed."""
    scope = (scope or "").strip().lower()
    severity = (severity or "").strip().lower()
    title = " ".join((title or "").split())
    bad = _bug_fields_problem(scope, severity, globs)
    if bad:
        return O.failed("bug.found", bad)
    log, cfg, st = _load(repo, agent)
    minted = IDS.mint(cfg, st, "bug", events=log.read_all, given=id, hash_parts=(summary, item))
    bid = minted.id
    chk = DD.check_add(
        repo,
        log,
        cfg,
        st,
        DD.Record(
            kind="bug", event_kind="bug.found", rid=bid, title=title, body=summary, item=item
        ),
        answer,
    )
    if chk.refusal is not None:
        return chk.refusal
    # Only what was said is written, so a plain re-report of a ddflow-scoped bug (same
    # id) does not turn it back to `project`; `--scope project` says it and does.
    extra = {k: v for k, v in (("title", title), ("severity", severity), ("scope", scope)) if v}
    if chk.extension:
        # The text goes onto the OPEN bug it was answered onto; what was said about its
        # title, severity or scope goes with it (a merge: an empty field never blanks one).
        target = chk.extension["target"]
        held = st.bugs.get(target)  # the target may be another kind of record
        with log.transaction():
            if extra and held is not None:
                log.append("bug.found", target, dict(extra))
            out = DD.extend(log, cfg, chk, "bug.found")
        offer = upstream_offer(scope or (held.scope if held else "project"), target)
        return O.ok("bug.found", **{**out.data, **({"offer": offer} if offer else {})})
    prior = st.bugs.get(bid)
    # The bug's own event names its fix task, and is written FIRST: the lock is no
    # rollback (each append is durable on its own), so the order decides what a crash
    # between the two leaves behind. A bug naming a task not yet filed is repaired by
    # `bug file-tasks`, which walks open bugs whose task is missing as well as those with
    # none, and by a re-report of the same id, which files the task the record names and
    # has not got; a task for a bug that was never recorded would be repaired by nothing.
    # Any other re-report (`prior`) files nothing: the record already has what it has.
    # What the reply then SAYS about the task -- claim it now, it is queued, ask -- is the
    # `[bugs].on_found` knob's business (B-bugs-fix-now), decided from `fix_task` here.
    fix: dict[str, Any] = {"fix_task": "", "filed": False}
    with log.transaction():
        # Decided from the log as it is NOW, under the lock: a task another process filed
        # (or claimed) since `_load` must be seen, or it is filed twice -- a second
        # definition of a live id, over its holder.
        st = fold(log.read_all(), strict=False)
        prior = st.bugs.get(bid)
        dangling = prior is not None and prior.open and bool(prior.fix_task)
        dangling = dangling and not _live(st, prior.fix_task)  # type: ignore[union-attr]
        filing = cfg.bugs.file_task and not no_task and (prior is None or dangling)
        fix_id = _fix_task_id(st, cfg, bid, item) if filing else ""
        linked = {"fix_task": fix_id} if fix_id else {}
        minted = IDS.confirm(cfg, "bug", minted, used=IDS.used_now(log), hash_parts=(summary, item))
        log.append(
            "bug.found",
            bid,
            {
                "item": item,
                "summary": summary,
                **extra,
                **linked,
                **IDS.key_field(minted),
                **chk.fields,
            },
        )
        DD.after_add(log, cfg, bid, chk)
        if filing:
            fix = _file_fix_task(
                log, cfg, st, bid, title=title, summary=summary, item=item, globs=globs
            )
    # A re-report merges into the record and never reopens it (see `_h_bug_found`). Said
    # out loud, because otherwise a real recurrence filed under an id already closed
    # vanishes without a word. Only an EXPLICIT `--id` can land on a closed record: an
    # auto id is time-salted (core/ids.py), so the same text without an id is a new id,
    # and the duplicate check above is what catches it.
    offer = upstream_offer(scope or (prior.scope if prior else "project"), bid)
    more: dict[str, Any] = {"offer": offer} if offer else {}
    more["fix_task"] = fix["fix_task"] or (prior.fix_task if prior else "")
    more["fix_task_filed"] = fix["filed"]
    if prior is not None and prior.resolution:
        return O.ok("bug.found", id=bid, resolution=prior.resolution, **more, **chk.data())
    return O.ok("bug.found", id=bid, **more, **chk.data())


#: A fix task's title is the bug's headline behind "Fix bug X:" -- the words `show <bug>`
#: already recognises as a fix's own claim (`api.reporting._show_bug`). Cut, not wrapped:
#: the whole summary is in the body.
_FIX_TITLE_MAX = 120


def _fix_task_of(st, cfg, item: str):
    """The OPEN bug-fix task ``item`` names, or None. A report filed against the task that
    is fixing it (`bug found --item <fix task>`, the dedupe's `filed_against`) names its
    fix; one filed against the item it was FOUND in -- a feature, a finished task, a
    phase -- names where to look, and gets a task of its own."""
    from ...core.flow import FEATURE, branch_kind

    it = st.items.get(item) if item else None
    if it is None or it.removed or it.kind != "task" or it.terminal:
        return None
    return it if branch_kind(it, cfg) != FEATURE else None


def _open_phase_of(st, item: str) -> str:
    """The nearest OPEN phase at or above ``item``, or "". A finished phase does not take
    new work: a task filed under it would sit open beneath a phase that says done."""

    it = st.items.get(item) if item else None
    if it is None or it.removed:
        return ""
    for node in (it, *st.ancestors(item)):
        if node.kind == "phase" and not node.terminal and not node.removed:
            return node.id
    return ""


def _own_fix_id(st, cfg, bug_id: str) -> str:
    """The bug's own fix task id (`[ids].fix_task`, `fix-<bug>` by default): one obvious
    name per bug, so a second report finds the task already there instead of filing a
    twin -- or, when that one was ABANDONED, which nothing revives, the first
    `[ids].fix_task_followup` (`fix-<bug>-2`, `-3`, ...) that is not abandoned too
    (B974e34fa83). Decided from the fold the caller holds inside its transaction, so the
    id is free when it is filed (L-free-id-before-add)."""
    from ...core.model import ABANDONED

    used = IDS.taken(st)
    # seq=1: the bug's OWN fix task is one fixed name (a `{seq}` in its template is the
    # first number), so a second report finds it instead of minting the next number
    tid = IDS.render(cfg, "fix_task", used=used, parent=bug_id, seq=1)
    n = 1
    while (it := st.items.get(tid)) is not None and not it.removed and it.state == ABANDONED:
        n += 1
        tid = IDS.render(cfg, "fix_task_followup", used=used, parent=bug_id, seq=n)
    return tid


def _fix_task_id(st, cfg, bug_id: str, item: str) -> str:
    """The id `_file_fix_task` will bind ``bug_id`` to: the open bug-fix task ``item``
    names, else its own fix task (`_own_fix_id`, whether or not it is already in the
    queue). One rule, so the bug event written before the task names the task that then
    gets filed."""
    named = _fix_task_of(st, cfg, item)
    return named.id if named is not None else _own_fix_id(st, cfg, bug_id)


def _has_fix_task(st, cfg, bug_id: str, item: str) -> bool:
    """Whether ``bug_id`` already has its fix in the queue, so `_file_fix_task` would file
    nothing: the open bug-fix task ``item`` names, or its own fix task, not abandoned."""
    if _fix_task_of(st, cfg, item) is not None:
        return True
    have = st.items.get(_own_fix_id(st, cfg, bug_id))
    return have is not None and not have.removed


def _file_fix_task(
    log, cfg, st, bug_id: str, *, title: str, summary: str, item: str, globs: str
) -> dict[str, Any]:
    """File the task that fixes ``bug_id`` and return ``{fix_task, filed, phase_made}``.

    The task: `fix-<bug>`, tagged a bug fix (the first of `[flow] bugfix_tags`, so
    `bugs_first` and gitflow both see it), under the item's open phase -- else the standing
    `[bugs] phase`, made on first use -- carrying ``globs`` or the item's, the item's
    priority and release line, and `fixes = [bug]`. Appends inside the caller's
    transaction; ``st`` is updated in place so a loop (`bug_file_tasks`) sees what it made.
    ``filed`` is False when the bug already has its fix: the open bug-fix task ``item``
    names, or a `fix-<bug>` already in the queue.
    """
    from ...api.items import DEFAULT_PRIORITY
    from ...core.model import Item

    tid = _fix_task_id(st, cfg, bug_id, item)
    if _has_fix_task(st, cfg, bug_id, item):
        return {"fix_task": tid, "filed": False, "phase_made": ""}
    # A removed source still lends its files, priority and line: the bug is in those files
    # whether or not the item that touched them is still in the queue, and the fix task's
    # globs are what makes a feature on them wait. Only the parent needs an OPEN phase.
    src = st.items.get(item) if item else None
    parent = _open_phase_of(st, item)
    phase_made = ""
    if not parent:
        standing = st.items.get(cfg.bugs.phase)
        if standing is None or standing.removed:
            phase_made = cfg.bugs.phase
            log.append(
                "phase.added",
                phase_made,
                {
                    "title": "Bugs",
                    "needs": [],
                    "globs": [],
                    "body": "Fix tasks for bugs found outside any open phase (`bug found`).",
                    "tags": [],
                    "priority": DEFAULT_PRIORITY,
                    "line": "",
                },
            )
            st.items[phase_made] = Item(id=phase_made, kind="phase", title="Bugs")
            parent = phase_made
        else:
            parent = _open_phase_of(st, standing.id)  # "" when the standing phase is done
    tags = list(cfg.flow.bugfix_tags)
    tag = "bugfix" if "bugfix" in tags else (tags[0] if tags else "bugfix")
    first = summary.strip().splitlines()[0] if summary.strip() else ""
    headline = " ".join((title or first).split())
    full_title = f"Fix bug {bug_id}: {headline}" if headline else f"Fix bug {bug_id}"
    if len(full_title) > _FIX_TITLE_MAX:
        full_title = full_title[: _FIX_TITLE_MAX - 3].rstrip() + "..."
    where = f" Found on {item}." if item else ""
    body = (
        f"Fixes bug {bug_id}: {summary.strip()}{where}\n\n"
        f"Write the regression test first and watch it FAIL on the unfixed code; then "
        f"`ddflow complete {tid} --regression-test <test>` closes the bug with the task "
        f"(or `ddflow bug fixed {bug_id} --regression-test <test>` first)."
    )
    data = {
        "parent": parent,
        "title": full_title,
        "needs": [],
        "globs": GS.parse(globs) if globs else list(src.globs if src else []),
        "body": body,
        "tags": [tag],
        "priority": src.priority if src else DEFAULT_PRIORITY,
        "line": src.line if src else "",
        "fixes": [bug_id],
    }
    log.append("task.added", tid, data)
    st.items[tid] = Item(id=tid, kind="task", title=full_title, parent=parent, fixes=[bug_id])
    return {"fix_task": tid, "filed": True, "phase_made": phase_made}


def _live(st, item: str) -> bool:
    """Whether ``item`` names an item in the queue (recorded and not removed)."""
    it = st.items.get(item) if item else None
    return it is not None and not it.removed


def _needs_fix_task(st, b, cfg=None) -> bool:
    """Whether open bug ``b`` has no fix task that will ever fix it: none in the queue; a
    DONE task it was merely reported against -- `bug found --item <open fix task>` links
    a report to that task, and the task's completion does not fix it (B8dcbf2f8da); or an
    ABANDONED task, which nothing sends back -- its own `fix-<bug>` included, refiled as
    `fix-<bug>-2` (`_own_fix_id`, B974e34fa83). Left alone: a done task's OWN bug
    (`verify --reopen` sends that task back)."""
    from ...core.model import ABANDONED, DONE
    from ...services.completion import fixes_of

    if not _live(st, b.fix_task):
        return True
    state = st.items[b.fix_task].state
    if state == ABANDONED:
        return True
    return state == DONE and b.id not in fixes_of(st, b.fix_task, cfg)


def bug_file_tasks(repo: Path, *, dry_run: bool = False, agent: str = "") -> O.Outcome:
    """Give every OPEN bug that has no fix task one -- the one-shot upgrade for a log
    written before `bug found` filed them, and the repair for a bug whose `fix_task` names
    an item that is not in the queue (a crash between the two appends of `bug found`) or a
    finished task it was only reported against (`_needs_fix_task`, B8dcbf2f8da). A
    bug whose item is an open bug-fix task is linked to it (``linked``); any other gets
    `fix-<bug>` filed (``filed``), as `bug found` would have. Nothing to do is exit 2.
    ``dry_run`` reports and writes nothing."""
    log, cfg, st = _load(repo, agent)
    filed: list[str] = []
    linked: list[str] = []
    tasks: dict[str, str] = {}
    #: {bug: {task, state}} for the linked ones: the task may be finished (B70d80555a4).
    links: dict[str, dict[str, str]] = {}
    with log.transaction():
        st = fold(log.read_all(), strict=False)
        todo = sorted(
            (b for b in st.bugs.values() if b.open and _needs_fix_task(st, b, cfg)),
            key=lambda b: (b.found_at, b.id),
        )
        for b in todo:
            if dry_run:
                # The same test the write path applies, so the prediction is the outcome.
                if _has_fix_task(st, cfg, b.id, b.item):
                    linked.append(b.id)
                    links[b.id] = _link(st, _fix_task_id(st, cfg, b.id, b.item))
                else:
                    filed.append(b.id)
                    tasks[b.id] = _fix_task_id(st, cfg, b.id, b.item)
                continue
            fix = _file_fix_task(
                log, cfg, st, b.id, title=b.title, summary=b.summary, item=b.item, globs=""
            )
            # A partial `bug.found` carries the link: the fold merges and never blanks
            # (`_h_bug_found`), and an older ddflow folds it as the record it already has.
            log.append("bug.found", b.id, {"fix_task": fix["fix_task"]})
            (filed if fix["filed"] else linked).append(b.id)
            if not fix["filed"]:
                links[b.id] = _link(st, fix["fix_task"])
            if fix["filed"]:
                # The id filed, not `fix-<bug>` assumed: an abandoned one gets a successor.
                tasks[b.id] = fix["fix_task"]
    if not filed and not linked:
        return O.nothing(
            "bug.file_tasks",
            "every open bug already has a fix task",
            filed=[],
            linked=[],
            tasks={},
            links={},
            dry_run=dry_run,
        )
    return O.ok(
        "bug.file_tasks", filed=filed, linked=linked, tasks=tasks, links=links, dry_run=dry_run
    )


def _link(st, task: str) -> dict[str, str]:
    """The task a bug was linked to, with its state (open, running, done, ...); "missing"
    should it not be in the queue, which a link is only made to when it is."""
    it = st.items.get(task)
    return {"task": task, "state": it.state if it is not None and not it.removed else "missing"}


def _bug_fields_problem(scope: str, severity: str, globs: str) -> str:
    """Why `bug found`'s optional fields cannot be recorded, or ""."""
    if scope and scope not in BUG_SCOPES:
        return f"unknown scope {scope!r}: one of {', '.join(BUG_SCOPES)}"
    if severity and severity not in BUG_SEVERITIES:
        return f"unknown severity {severity!r}: one of {', '.join(BUG_SEVERITIES)}"
    return GS.problem(GS.parse(globs))


def _unknown_bug(kind: str, bid: str, st) -> O.Outcome:
    """The refusal for an id that names no recorded bug, shared by every closure.

    An unknown id used to be closed anyway, folding a phantom bug while the real one
    stayed open -- typically the TASK id, passed because `bug found --item` links one.
    """
    linked = sorted(b.id for b in st.bugs.values() if b.item == bid and b.open)
    hint = (
        f" Open bugs linked to {bid}: {', '.join(linked)} -- close those ids."
        if linked
        else " `ddflow recall` or `ddflow status` lists the open bugs."
    )
    return O.refused(kind, f"no bug {bid} is recorded in this log.{hint}", id=bid)
