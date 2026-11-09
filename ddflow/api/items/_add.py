"""Adding phases and tasks, and closing imported phases (`phase_add`, `task_add`)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import ddflow.api._dedupe as DD
from ddflow.api import items as _items

from ...config import csv_list
from ...core import flow as F
from ...core import globspec as GS
from ...core import outcome as O
from ...core.defaults import DEFAULT_PRIORITY
from ...services import choices as CH
from ...services import items as IT
from ...services import leases as L
from ...services import phase_close as PC
from ...services import phase_complete as PCC
from ._edit import _bad_line


def phase_complete(repo: Path, item: str, *, approval: str = "", agent: str = "") -> O.Outcome:
    """Retire a phase whose every task is done or abandoned, for a person directly and
    for an agent only by an operator approval.

    Distinct from `phase close` (imported work only) and from `complete` (an item's own
    pipeline): this is the deliberate retire, gated behind the operator for an agent, and it
    records `phase.completed` with the evidence. The api loads the config too, which the
    approval gate reads for the use window.
    """
    log, cfg, _st = _items._load(repo, agent)
    return PCC.complete(log, cfg, item, approval=approval, agent=agent)


def phase_close(repo: Path, item: str, *, imported: bool = False, agent: str = "") -> O.Outcome:
    """Close an IMPORTED phase whose work predates ddflow, without a phase gate pipeline.

    For a phase `ddflow import` created whose every task is done or abandoned: it records
    `item.completed` with the evidence, exactly as the importer does for the work it closes
    on the way in, so `brief` stops nagging "finished but still open" about work that cannot
    run this project's pipeline. `imported` must be True: the flag is the authority being
    used, and it is what marks this apart from running the phase's own pipeline.
    """
    log, _cfg, _st = _items._load(repo, agent)
    return PC.close(log, item, imported=imported, agent=agent)


def phase_add(  # noqa: PLR0913 -- BACKLOG B179: the same draft record as task_add
    repo: Path,
    item: str,
    *,
    title: str = "",
    needs: str = "",
    globs: str = "",
    body: str = "",
    tags: str = "",
    priority: int = DEFAULT_PRIORITY,
    line: str = "",
    readd: bool = False,
    answer: DD.Answer | None = None,
    agent: str = "",
    allow_unknown_needs: bool = False,
) -> O.Outcome:
    """Add a phase — an umbrella that completes when its tasks do.

    ``line`` puts the whole phase on a release line; its tasks inherit it. An id already
    in the queue is REFUSED; ``readd`` lets a removed one come back. A phase that reads
    like an existing record is refused until ``answer`` says what it is (``_dedupe``).
    """
    bad = IT.bad_id(item) or IT.bad_title(title) or GS.problem(GS.parse(globs))
    if bad:
        return O.failed("phase.added", bad, id=item)
    log, cfg, st = _items._load(repo, agent)
    bad = _bad_line(cfg, line) if line else ""
    if bad:
        return O.failed("phase.added", bad, id=item)
    bad = IT.unknown_needs(st, csv_list(needs)) if not allow_unknown_needs else ""
    if bad:
        return O.failed("phase.added", bad, id=item)
    with log.transaction():
        st = L._fold(log)
        taken = IT.taken(st, item, readd=readd)
        if taken:
            return O.refused("phase.added", taken, id=item)
        # Inside the transaction, against the log as it is NOW: a record another agent
        # filed since `_load` is seen.
        chk = DD.check_add(
            repo,
            log,
            cfg,
            st,
            DD.Record(kind="phase", event_kind="phase.added", rid=item, title=title, body=body),
            answer,
        )
        if chk.refusal is not None:
            return chk.refusal
        if chk.extension:
            return DD.extend(log, cfg, chk, "phase.added", st=st, globs=GS.parse(globs))
        log.append(
            "phase.added",
            item,
            {
                "title": title,
                "needs": csv_list(needs),
                "globs": GS.parse(globs),
                "body": body,
                "tags": csv_list(tags),
                "priority": priority,
                "line": line,
                **chk.fields,
            },
        )
        DD.after_add(log, cfg, item, chk)
    return O.ok("phase.added", id=item, **chk.data())


def _port_of_lines(st, cfg, port_of: str, wanted: list[str]) -> tuple[list[str], str]:
    """The lines a follow-up to ``port_of`` must reach (B180), or the reason it cannot."""

    if wanted:
        return wanted, "--port-of takes its lines from that fix: drop --line/--lines"
    origin = st.items.get(port_of)
    if origin is None:
        return [], f"--port-of: no such item {port_of!r}"
    if origin.removed:
        return [], f"--port-of: {origin.id!r} was removed"
    origin = st.items.get(origin.port_of) or origin
    if origin.removed:
        return [], f"--port-of: {origin.id!r} was removed"
    family = [origin, *(o for o in st.items.values() if o.port_of == origin.id and not o.removed)]
    lines = (F.effective_line(st, o) or cfg.flow.current_line for o in family)
    return list(dict.fromkeys(lines)), ""


def _wanted_lines(st, cfg, line: str, lines: str, port_of: str) -> tuple[list[str], str]:
    """The release lines an added task must reach, or the reason it cannot."""
    wanted = csv_list(lines) or ([line] if line else [])
    if port_of:
        wanted, bad = _port_of_lines(st, cfg, port_of, wanted)
        if bad:
            return [], bad
    for ln in wanted:
        if bad := _bad_line(cfg, ln):
            return [], bad
    return wanted, ""


def _add_ports(
    log, st, cfg, base, plan, title: str, tags: str, readd: bool
) -> tuple[list[str], str]:
    """Write one port task per other release line of ``plan``: (their ids, the problem)."""
    item = base.id
    ports: list[str] = []
    for ln, frm in plan.ports if plan else []:
        source = item if frm < 0 else ports[frm]
        pid = f"{item}@{ln}"
        port = replace(
            base,
            id=pid,
            title=f"{title or item} (port to {ln})",
            needs=[source],
            line=ln,
            tags=[*csv_list(tags), "port"],
            extra={
                "port_of": item,
                "port_from": source,
                "port_strategy": plan.strategy,
            },
        )
        added = IT.add_task(
            log,
            st,
            cfg,
            port,
            dedupe="a port of the fix just checked: one per release line",
            readd=readd,
        )
        if not added.ok:
            return ports, added.problem
        ports.append(pid)
    return ports, ""


def _kind_field(kind: str) -> str | None:
    """The payload's `kind`: None (not written) for a plain task."""
    return kind if kind and kind != "task" else None


def _csv_none_when_empty(v: str) -> list[str] | None:
    """A comma list, or None (field not written) when empty."""
    return csv_list(v) or None


def _edges_problem(st, parent: str, needs: str, allow_unknown_needs: bool) -> str:
    """Why the new task's parent or ``needs`` cannot stand, or ""."""
    bad = IT.missing_parent(st, parent)
    if bad or allow_unknown_needs:
        return bad
    return IT.unknown_needs(st, csv_list(needs))


def _edge_refusal(st, parent: str, bad: str, item: str) -> O.Outcome:
    """The task-add reply for an edge that cannot stand: a CLOSED (imported) phase refuses,
    a typo'd parent or need fails."""
    if IT.closed_phase_parent(st, parent):
        return O.refused("task.added", bad, id=item)
    return O.failed("task.added", bad, id=item)


def task_add(  # noqa: PLR0913 -- BACKLOG B179: a TaskDraft record, as decisions have
    repo: Path,
    item: str,
    *,
    title: str = "",
    parent: str = "",
    needs: str = "",
    globs: str = "",
    body: str = "",
    tags: str = "",
    priority: int | None = None,
    line: str = "",
    lines: str = "",
    port_of: str = "",
    resources: str = "",
    kind: str = "",
    readd: bool = False,
    answer: DD.Answer | None = None,
    agent: str = "",
    allow_unknown_needs: bool = False,
) -> O.Outcome:
    """Add a task. Its parent may be a phase OR another task (making it a sub-task).

    ``line`` puts it on one release line. ``lines`` (comma-separated) files a fix that
    must reach SEVERAL: the task is written on the line `port_strategy` dictates and a
    port item is generated for each other line -- `<id>@<line>`, an ordinary task with
    its own branch, gates and merge, which starts once what it carries has landed.
    ``port_of`` names an earlier fix: this is a FOLLOW-UP to it and takes the lines that
    fix reached (B180), so its ports carry what THIS one lands. Naming one of the earlier
    fix's ports resolves to the fix. It cannot be combined with ``line``/``lines``.

    Tasks can be added at ANY time, including while their parent is being worked: a task
    that turns out to contain two things is the normal case, not an exception, and a
    queue that cannot absorb that discovery pushes the work into someone's head.

    An id already in the queue is REFUSED: adding never changes an item, `update` does.
    ``readd`` lets a REMOVED id come back with the new definition.

    A task that reads like an existing record is REFUSED until ``answer`` says what it is
    (``api._dedupe``): ``new``, or ``extends`` / ``duplicate_of`` / ``related`` a record.
    ``answer`` is one bundled parameter, not three (BACKLOG B179).
    """

    base = IT.TaskDraft(
        item,
        parent=parent,
        globs=GS.parse(globs),
        body=body,
        tags=csv_list(tags),
        priority=priority,
        resources=_csv_none_when_empty(resources),
        kind=_kind_field(kind),
    )
    bad = IT.problem_of_add(base, title)
    if bad:
        return O.failed("task.added", bad, id=item)
    log, cfg, st = _items._load(repo, agent)
    bad = _edges_problem(st, parent, needs, allow_unknown_needs)
    if bad:
        return _edge_refusal(st, parent, bad, item)
    wanted, bad = _wanted_lines(st, cfg, line, lines, port_of)
    if bad:
        return O.failed("task.added", bad, id=item)
    with log.transaction():
        st = L._fold(log)
        taken = IT.taken(st, item, readd=readd)
        if taken:
            return O.refused("task.added", taken, id=item)
        chk = DD.check_add(
            repo,
            log,
            cfg,
            st,
            DD.Record(
                kind="task", event_kind="task.added", rid=item, title=title, body=body, item=parent
            ),
            answer,
        )
        if chk.refusal is not None:
            return chk.refusal
        if chk.extension:
            return DD.extend(log, cfg, chk, "task.added", st=st, globs=GS.parse(globs))
        plan = None
        adopted: list[str] = []
        if len(set(wanted)) > 1:
            clash = [f"{item}@{ln}" for ln in wanted if f"{item}@{ln}" in st.items]
            if clash:
                return O.failed("task.added", f"{', '.join(clash)} already exist", id=item)
            # The first fix filed across lines is where the strategy starts to matter: an
            # unmade choice is defaulted HERE, on the record, and followed from now on.
            adopted = CH.adopt_defaults(log, cfg, ["port_strategy"])
            plan = F.plan_ports(cfg, wanted, cfg.flow.port_strategy)
        author_line = plan.author if plan else (wanted[0] if wanted else "")
        main = replace(base, title=title, needs=csv_list(needs), line=author_line)
        added = IT.add_task(log, st, cfg, main, dedupe=chk, readd=readd)
        if not added.ok:
            return (O.refused if added.refused else O.failed)("task.added", added.problem, id=item)
        DD.after_add(log, cfg, item, chk)
        ports, bad = _add_ports(log, st, cfg, base, plan, title, tags, readd)
        if bad:
            return O.failed("task.added", bad, id=item)
        # Giving a task its first child turns it into an umbrella: its lease is released
        # (`split` does the same), see `IT.release_umbrella`.
        released = IT.release_umbrella(
            log, st, parent, note=f"became an umbrella when {item} was added"
        )
    return O.ok(
        "task.added",
        id=item,
        parent=parent,
        released_parent_lease=released,
        line=plan.author if plan else (wanted[0] if wanted else ""),
        ports=ports,
        port_strategy=plan.strategy if plan else "",
        port_note=plan.note if plan else "",
        defaulted=adopted,
        **chk.data(),
    )
