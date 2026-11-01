"""The `ddflow upgrade --apply` loop: choose the plan's items and run them, one category
at a time, with a backup first.

Split out of ``ddflow/services/upgrade_apply.py``: `apply` here is the entry point the
plan (`services.upgrade_plan`) feeds. Per-item appliers live in `._appliers`, the shared
status words and mode policies in `._constants`. The closing `upgrade.applied` event is
appended here (`_record`), so replay explains the upgrade.
"""

from __future__ import annotations

import contextlib
import re
import sys
import uuid
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...config import Config
from ...core.digest import of_obj
from ...core.events import UNSTAMPED, UPGRADE_APPLIED_KIND, stamp_text
from ...core.model import State
from .. import adopt as AD
from .. import migrations as MG
from .. import repairs as RP
from .. import upgrade_plan as UP
from .. import upgrade_written as UW
from ..backups import SnapshotRefused, prune
from ._appliers import parse_categories, touched
from ._constants import (
    _VERSIONED,
    ACKNOWLEDGED,
    APPLIED,
    BACKUP_MODES,
    CONFIG_POLICIES,
    FAILED,
    REFUSED,
    SKIPPED,
    UNAVAILABLE,
)

# -- the apply ---------------------------------------------------------------------------------


def _confirmed(item: dict[str, Any], confirm: Mapping[str, str]) -> str:
    """The reason the operator gave for this item, "" when they gave none."""
    for k in (
        item["id"],
        item.get("key"),
        item.get("repair"),
        item.get("migration"),
        item.get("path"),
    ):
        if k and confirm.get(k):
            return confirm[k]
    return ""


def _needs_confirmation(item: dict[str, Any], config_changes: str) -> bool:
    if item["action"] == UP.OPERATOR:
        return True
    return item["category"] == "config" and item["action"] == UP.AGENT and config_changes != "agent"


def apply(
    repo: Path,
    log: Any,
    cfg: Config,
    st: State,
    *,
    categories: str | Collection[str] | None = None,
    confirm: Mapping[str, str] | None = None,
    backup: str = "local",
    agent: str = "",
    plan: dict[str, Any] | None = None,
    watch: Watch | None = None,
) -> dict[str, Any]:
    """Apply the chosen categories of the plan (see the module docstring).

    Every run that has something to do is recorded as it goes (`upgrade.applied` with a
    ``run`` id: ``started``, one ``step`` per item, then the closing event), so one that is
    interrupted is shown by `ddflow upgrade --status` and finished by ``watch.resume`` (the run's id,
    from ``services.upgrade_status``): the steps it already settled are not run again.

    Returns plain data: ``results`` (one record per item: id, category, status, detail),
    ``refused``/``failed`` counts, ``backup`` (the directory, "" when nothing was written),
    ``from``/``to``, ``noop`` and the ``text`` for a person. ``exit`` is 0 when everything
    chosen was applied, acknowledged or already fine, 1 when an applier failed, 3 when an
    item waits for the operator's confirmation, 2 when a step could not run (a repair whose
    detector was unavailable). Raises ValueError for an unknown category or
    ``backup`` value. Who may apply a config change is the `[upgrade].config_changes` knob
    (``cfg.upgrade``), the same one the plan reads."""
    repo = Path(repo)
    config_changes = cfg.upgrade.config_changes  # the one source: the plan reads it too
    _check_modes(backup, config_changes)
    watch = watch or Watch()
    # a resume keeps what the operator confirmed before the interruption
    prior_reasons = (st.upgrade_runs.get(watch.resume) or {}).get("confirmed") or {}
    confirm = {**prior_reasons, **dict(confirm or {})}
    plan = plan or UP.build(repo, log, cfg, st)
    frm, to = plan["project_version"], plan["running"]
    results, todo = _select(plan, parse_categories(categories), confirm, config_changes)
    earlier = _settled_steps(st, watch.resume)
    selected = todo
    todo = [i for i in selected if i["id"] not in earlier]
    # what the interrupted run settled is settled: not judged again (an operator item it applied
    # with a confirmation is not refused now) and not able to un-settle its category
    results = [r for r in results if r["id"] not in earlier]

    files = [p for i in todo for p in touched(repo, cfg, i)]
    # A resume keeps the restore point the interrupted run made (it holds the originals of the
    # whole run): a second, partial one would be the newest, and `--restore` would undo only half.
    prior = str((st.upgrade_runs.get(watch.resume) or {}).get("backup") or "")
    if prior:
        backup_dir, why, refused = prior, "", False
    else:
        _do_save = sys.modules[__package__]._save  # late-bound: honours monkeypatch of the package attr

        backup_dir, why, refused = _do_save(repo, cfg, backup, files, frm, to)
    if why:
        return _not_saved(results, todo, why, refused, frm)

    saved = {p.resolve() for p in files} if backup_dir else set()
    before = UW.digests(repo, files)  # what the run changes is what it records as written
    late_backup = _late_saver(repo, cfg, backup, (frm, to), saved, repo_before=before)

    run = _start(log, watch, todo, (frm, to), backup_dir, confirm)
    results += _execute(
        repo, log, cfg, todo, agent, late_backup, _stepper(repo, log, run, watch, before)
    )
    ok, done_cats, reasons = _summarise(results, selected, confirm)
    ok |= earlier  # settled by the interrupted run this one finishes
    reasons = _reasons(selected, ok, confirm)
    done_cats = _with_earlier(done_cats, plan, earlier, results)
    new_to = (frm or UNSTAMPED) if _unresolved(plan, ok) else to
    lags = _lags(results, plan, frm, to)
    wrote = UW.written(repo, before)
    _close(log, run, selected, ok, (frm, new_to, to), done_cats, backup_dir, reasons, lags, wrote)
    return _finish(results, backup_dir, frm, new_to, done_cats, reasons)


def _lags(results: list[dict[str, Any]], plan: dict[str, Any], frm: str, to: str) -> bool:
    """Is the stamp all that is left to do? This run found nothing to apply (notes are only reported) and everything
    versioned was settled by earlier partial applies (the plan no longer lists it), yet the
    project is still at ``frm``: without the move it stayed at its old version for good
    (upgrade-plan-never-empties)."""
    # a note that is only reported (SKIPPED) is nothing to apply
    idle = all(r["status"] == SKIPPED for r in results)
    return idle and frm != to and not _unresolved(plan, set())


def _not_saved(
    results: list[dict[str, Any]], todo: list[dict[str, Any]], why: str, refused: bool, frm: str
) -> dict[str, Any]:
    """Nothing was changed: the originals could not be saved, so no item is applied."""
    status = REFUSED if refused else FAILED
    failed = results + [_rec(i, status, why) for i in todo]
    return _finish(failed, "", frm, frm or UNSTAMPED, [], {})


def _start(
    log: Any,
    watch: Watch,
    todo: list[dict[str, Any]],
    versions: tuple[str, str],
    backup: str,
    confirm: Mapping[str, str],
) -> str:
    """The run's id (the interrupted run's when resuming, else a new one), and its first record."""
    if not todo:
        return watch.resume
    run = watch.resume or uuid.uuid4().hex[:10]
    frm, to = versions
    _note(
        log,
        run,
        "started",
        source=frm,
        target=to,
        categories=_cats_of(todo),
        items=_ids(_in_order(todo)),
        backup=backup,
        confirmed={_ident(i): why for i in todo if (why := _confirmed(i, confirm))},
    )
    return run


def _with_earlier(
    cats: list[str], plan: dict[str, Any], earlier: set[str], results: list[dict[str, Any]]
) -> list[str]:
    """``cats`` plus the categories an interrupted run settled steps of (``earlier``), unless
    a step of that category did not settle in this run."""
    unsettled = {r["category"] for r in results if r["status"] not in (APPLIED, ACKNOWLEDGED)}
    more = _earlier_cats(plan, earlier) - unsettled
    return [c for c in UP.CATEGORIES if c in cats or c in more]


def _late_saver(
    repo: Path,
    cfg: Config,
    mode: str,
    versions: tuple[str, str],
    saved: set[Path],
    repo_before: dict[str, str | None],
) -> Callable[[list[Path]], str]:
    """The function a migration calls with files its own detect now plans that the plan's did
    not name (earlier steps of a run can change what it finds): they are saved too, in the same
    mode, so none is rewritten without a copy."""

    def late_backup(more: list[Path]) -> str:
        for rel, d in UW.digests(repo, more).items():
            repo_before.setdefault(rel, d)  # untouched until now: its bytes are the "before"
        fresh = [p for p in more if p.resolve() not in saved]
        if not fresh or mode == "none":
            return ""
        _do_save = sys.modules[__package__]._save  # late-bound: honours monkeypatch of the package attr

        where, why, _refused = _do_save(repo, cfg, mode, fresh, *versions)
        if why:
            raise OSError(why)
        saved.update(p.resolve() for p in fresh)
        return where

    return late_backup


@dataclass
class Watch:
    """Who follows an apply: the id of the interrupted run it finishes, and a callback that
    hears each step as it ends."""

    resume: str = ""
    progress: Callable[[str], None] | None = None


def _close(
    log: Any,
    run: str,
    todo: list[dict[str, Any]],
    ok: set[str],
    versions: tuple[str, str, str],
    cats: list[str],
    backup_dir: str,
    reasons: dict[str, str],
    stamp_only: bool = False,
    wrote: dict[str, str] | None = None,
) -> None:
    """End the run's record: the upgrade itself when something was settled (or only the
    stamp was left to move), else a failure."""
    frm, new_to, to = versions
    if ok or stamp_only:
        _record(log, todo, ok, frm, new_to, cats, backup_dir, reasons, run, wrote)
    elif run:
        _note(
            log,
            run,
            "failed",
            summary=f"upgrade {frm or 'unstamped'} -> {to}: nothing settled",
            written=wrote or {},
        )


def _ids(items: list[dict[str, Any]]) -> list[str]:
    return [i["id"] for i in items]


def _cats_of(items: list[dict[str, Any]]) -> list[str]:
    return [c for c in UP.CATEGORIES if any(i["category"] == c for i in items)]


def _earlier_cats(plan: dict[str, Any], ids: set[str]) -> set[str]:
    return {c for c, items in plan["categories"].items() for i in items if i["id"] in ids}


def _settled_steps(st: State, run: str) -> set[str]:
    """The ids of the steps the interrupted run ``run`` already applied or acknowledged."""
    steps = (st.upgrade_runs.get(run) or {}).get("steps") or [] if run else []
    return {s["id"] for s in steps if s["status"] in (APPLIED, ACKNOWLEDGED)}


def _note(log: Any, run: str, status: str, **fields: Any) -> None:
    """A progress record of ``run``: the same kind as the upgrade itself, told apart by `status`."""
    log.append(UPGRADE_APPLIED_KIND, "upgrade", {"run": run, "status": status, **fields})


def _stepper(
    repo: Path, log: Any, run: str, watch: Watch, before: dict[str, str | None]
) -> Callable[[dict[str, Any]], None]:
    """The callback `_execute` calls as each item ends: it records the step together with
    everything the run has written so far, so a run that is interrupted (and never reaches its
    closing event) still leaves the digests of the files it changed."""

    def on_step(rec: dict[str, Any]) -> None:
        _step(log, run, rec, watch.progress, UW.written(repo, before))

    return on_step


def _step(
    log: Any,
    run: str,
    rec: dict[str, Any],
    progress: Callable[[str], None] | None = None,
    wrote: dict[str, str] | None = None,
) -> None:
    """One item's outcome, recorded the moment it is known: what an interruption leaves."""
    if run:
        _note(
            log,
            run,
            "step",
            step=rec["id"],
            step_status=rec["status"],
            detail=rec["detail"][:300],
            written=wrote or {},
        )
    if progress:
        progress(f"[{rec['status']}] {rec['id']}")


def _summarise(
    results: list[dict[str, Any]], todo: list[dict[str, Any]], confirm: Mapping[str, str]
) -> tuple[set[str], list[str], dict[str, str]]:
    """`(ids settled, categories with something settled, the operator's reasons)`."""
    ok = {r["id"] for r in results if r["status"] in (APPLIED, ACKNOWLEDGED)}
    cats = [c for c in UP.CATEGORIES if any(r["category"] == c and r["id"] in ok for r in results)]
    return ok, cats, _reasons(todo, ok, confirm)


def _reasons(
    items: list[dict[str, Any]], ok: set[str], confirm: Mapping[str, str]
) -> dict[str, str]:
    """What the operator confirmed, for the items that were settled."""
    return {_ident(i): why for i in items if i["id"] in ok and (why := _confirmed(i, confirm))}


def _save(
    repo: Path, cfg: Config, mode: str, files: list[Path], frm: str, to: str
) -> tuple[str, str, bool]:
    """Save the originals in ``mode`` before anything is written: `(where, "", False)`, or
    `("", why, refused)` when they could not be saved -- ``refused`` when the snapshot policy
    said no (no git, a dirty tree), not when a disk failed. Nothing to save, or `none`, is
    `("", "", False)`."""
    if not files or mode == "none":
        return "", "", False
    try:
        if mode == "snapshot":
            _make_snapshot = sys.modules[__package__].make_snapshot  # late-bound: honours monkeypatch

            snap = _make_snapshot(repo, files, frm, to)
            extra = f" (and {snap.local} for what git cannot hold)" if snap.local else ""
            where = snap.tag + extra
        else:
            _make_backup = sys.modules[__package__].make_backup  # late-bound: honours monkeypatch

            where = str(_make_backup(repo, files, frm, to))
    except SnapshotRefused as exc:
        return "", str(exc), True
    except OSError as exc:
        return "", f"no backup could be written ({exc}); nothing was changed", False
    with contextlib.suppress(OSError):  # tidying old backups never fails an upgrade
        prune(repo, cfg.upgrade.backup_keep)
    return where, "", False


def _check_modes(backup: str, config_changes: str) -> None:
    if backup not in BACKUP_MODES:
        raise ValueError(f"unknown backup mode {backup!r}; known: {', '.join(BACKUP_MODES)}")
    if config_changes not in CONFIG_POLICIES:
        raise ValueError(
            f"unknown config_changes policy {config_changes!r}; known: {', '.join(CONFIG_POLICIES)}"
        )


def _record(
    log: Any,
    todo: list[dict[str, Any]],
    ok: set[str],
    frm: str,
    new_to: str,
    cats: list[str],
    backup_dir: str,
    reasons: dict[str, str],
    run: str = "",
    wrote: dict[str, str] | None = None,
) -> None:
    """Append the closing `upgrade.applied` event: replay's account of this upgrade."""
    log.append(
        UPGRADE_APPLIED_KIND,
        "upgrade",
        {
            **({"run": run, "status": "done"} if run else {}),
            "from": frm,
            "to": new_to,
            "categories": cats,
            "backup": backup_dir,
            "items": sorted(ok),
            "confirmed": reasons,
            "written": wrote or {},
            "config_changes": [
                {
                    **{k: i[k] for k in ("key", "change", "old", "new")},
                    "digest": of_obj(i.get("new")),
                }
                for i in todo
                if i["category"] == "config" and i["id"] in ok
            ],
            # `stamp_text`, not the raw fields: a partial apply on an unstamped project
            # records the marker as `to`, and the recorded summary is rendered verbatim by
            # `upgrade --status`. The marker is not a release (B439c42848f).
            "summary": (f"upgrade {stamp_text(frm)} -> {stamp_text(new_to)}: {', '.join(cats)}"),
        },
    )


def _ident(item: dict[str, Any]) -> str:
    return (
        item.get("key")
        or item.get("repair")
        or item.get("migration")
        or item.get("path")
        or item["id"]
    )


def _select(
    plan: dict[str, Any],
    chosen: list[str],
    confirm: Mapping[str, str],
    config_changes: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """`(results already settled, items to apply)`: a note outside the versioned categories
    is skipped and an item that needs the operator's confirmation, and has none, is refused."""
    results: list[dict[str, Any]] = []
    todo: list[dict[str, Any]] = []
    for cat in chosen:
        for item in plan["categories"].get(cat) or []:
            if item["action"] == UP.NOTE and cat not in _VERSIONED:
                # a contested record is the operator's: the exact command is printed, not run
                said = (
                    f"{item['summary']} -> {item['fix']}" if cat == "records" else item["summary"]
                )
                results.append(_rec(item, SKIPPED, said))
            elif _needs_confirmation(item, config_changes) and not _confirmed(item, confirm):
                results.append(_rec(item, REFUSED, _refusal(item, config_changes)))
            else:
                todo.append(item)
    return results, todo


def _execute(
    repo: Path,
    log: Any,
    cfg: Config,
    todo: list[dict[str, Any]],
    agent: str,
    late_backup: Callable[[list[Path]], str] | None = None,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    """Run each item's applier, one at a time.
    ``on_step`` hears each outcome as soon as it is known."""
    out: list[dict[str, Any]] = []
    # One item at a time, so each is recorded the moment it ends: an interruption leaves the
    # ones before it settled, and a resume does not repeat them.
    for item in _in_order(todo):
        cat = item["category"]
        if cat == "repairs":
            _do_repairs = sys.modules[__package__]._run_repairs  # late-bound: honours monkeypatch

            records = _do_repairs(repo, log, cfg, [item])
        elif cat == "migrations":
            records = _run_migrations(repo, log, cfg, [item], late_backup)
        elif cat == "instructions":
            records = _run_instructions(repo, [item])
        else:
            _do_run = sys.modules[__package__]._run  # late-bound: honours monkeypatch of the package attr

            records = [_rec(item, *_do_run(repo, cfg, item, agent))]
        out.extend(records)
        for r in records:
            if on_step:
                on_step(r)
    return out


#: The categories that run after every other one, in this order.
_LAST = ("repairs", "migrations", "instructions")


def _in_order(todo: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The items in the order they are executed (and recorded in the run's `started` event)."""
    rest = [i for i in todo if i["category"] not in _LAST]
    return rest + [i for c in _LAST for i in todo if i["category"] == c]


def _refusal(item: dict[str, Any], config_changes: str) -> str:
    key = _ident(item)
    why = "needs the operator's confirmation"
    if item["category"] == "config" and item["action"] == UP.AGENT:
        why += f" ([upgrade].config_changes = {config_changes})"
    return f"{why}: ddflow upgrade --apply --confirm {key} --reason '<why>'"


def _run_repairs(repo: Path, log: Any, cfg: Config, items: list[dict[str, Any]]) -> list[dict]:
    ids = [i["repair"] for i in items]
    try:
        records = RP.apply(repo, log, cfg, ids)
    except (OSError, KeyError, ValueError) as exc:
        return [_rec(i, FAILED, f"{type(exc).__name__}: {exc}") for i in items]
    by_repair = {r["repair"]: r for r in records}
    out = []
    for i in items:
        r = by_repair.get(i["repair"])
        if r is None:
            out.append(_rec(i, SKIPPED, "nothing left to repair"))
        elif r.get("unavailable"):
            out.append(_rec(i, UNAVAILABLE, f"could not run: {r['unavailable']}"))
        else:
            out.append(_rec(i, APPLIED, f"{len(r['findings'])} finding(s) settled"))
    return out


def _run_migrations(
    repo: Path,
    log: Any,
    cfg: Config,
    items: list[dict[str, Any]],
    late_backup: Callable[[list[Path]], str] | None = None,
) -> list[dict[str, Any]]:
    """Run the chosen migrations. The files their plans named were saved by `apply` (in the
    mode the caller chose, `none` included) before anything ran; ``late_backup`` saves any
    other file a migration now plans to rewrite, so none is rewritten without a copy."""
    try:
        outcomes = MG.run(
            repo, log, cfg, [i["migration"] for i in items], backup=late_backup or (lambda _f: "")
        )
    except (OSError, KeyError, ValueError) as exc:
        return [_rec(i, FAILED, f"{type(exc).__name__}: {exc}") for i in items]
    by_id = {o.migration: o for o in outcomes}
    out = []
    for i in items:
        o = by_id.get(i["migration"])
        if o is None:
            out.append(_rec(i, SKIPPED, "nothing left to migrate"))
            continue
        detail = "; ".join([o.detail, *o.problems])
        out.append(
            _rec(
                i, {MG.APPLIED: APPLIED, MG.UNAVAILABLE: UNAVAILABLE}.get(o.status, FAILED), detail
            )
        )
    return out


def _owner(action: str, items: list[dict[str, Any]]) -> str:
    """The item an action is about: the one whose project path the action names. When it
    names several (Aider's "added AGENTS.md to read: in .aider.conf.yml" names the file it
    pointed at, then the file it wrote), the one named LAST is the file written."""
    named = []
    for i in items:
        # whole path only: `replit.md` is not the tail of `docs/ddflow/drivers/deltas/replit.md`
        hits = list(re.finditer(rf"(?<![\w./-]){re.escape(i['path'])}(?![\w.-])", action))
        if hits:
            named.append((hits[-1].start(), i["path"]))
    return max(named)[1] if named else ""


def _run_instructions(repo: Path, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    paths = [i["path"] for i in items]
    try:
        actions = AD.refresh_docs(repo, only=paths, backup=False)
    except (OSError, ValueError) as exc:
        return [_rec(i, FAILED, f"{type(exc).__name__}: {exc}") for i in items]
    out = []
    for i in items:
        mine = [a for a in actions if _owner(a, items) == i["path"]]
        if any(isinstance(a, AD.Refused) for a in mine):
            # A refusal (a newer format, broken markers) is never counted as applied.
            out.append(_rec(i, REFUSED, "; ".join(mine)))
        else:
            out.append(_rec(i, APPLIED, "; ".join(mine) or "refreshed"))
    return out


def _rec(item: dict[str, Any], status: str, detail: str) -> dict[str, Any]:
    return {
        "id": item["id"],
        "category": item["category"],
        "key": item.get("key", ""),
        "status": status,
        "detail": detail,
    }


def _unresolved(plan: dict[str, Any], settled: set[str]) -> bool:
    """Is any item of the versioned categories still pending after this run?"""
    return any(i["id"] not in settled for c in _VERSIONED for i in plan["categories"].get(c) or [])


def _finish(
    results: list[dict[str, Any]],
    backup_dir: str,
    frm: str,
    to: str,
    cats: list[str],
    reasons: dict[str, str],
) -> dict[str, Any]:
    counts = {
        s: sum(1 for r in results if r["status"] == s) for s in (REFUSED, FAILED, UNAVAILABLE)
    }
    changed = [r for r in results if r["status"] in (APPLIED, ACKNOWLEDGED)]
    # A step that could not run is never read as done: 2 is "could not run", not 0.
    exit_code = (
        1 if counts[FAILED] else (3 if counts[REFUSED] else (2 if counts[UNAVAILABLE] else 0))
    )
    lines = []
    if not results:
        lines.append("Up to date: nothing to apply.")
    for r in results:
        lines.append(f"  [{r['status']}] {r['id']}: {r['detail']}")
    if backup_dir:
        lines.append(f"backup: {backup_dir}")
        lines.append("review what changed with: git diff")
    return {
        "from": frm,
        "to": to,
        "categories": cats,
        "results": results,
        "applied": len(changed),
        "refused": counts[REFUSED],
        "failed": counts[FAILED],
        "unavailable": counts[UNAVAILABLE],
        "backup": backup_dir,
        "confirmed": reasons,
        "noop": not results,
        "exit": exit_code,
        "text": "\n".join(lines),
    }
