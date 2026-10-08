"""`ddflow upgrade --apply`: do what `upgrade --plan` lists, with a backup first.

Decisions D-upgrade-model (3), D-upgrade-backups and D-upgrade-config-changes, task
B-upgrade.4-apply. The plan (`services.upgrade_plan`) says WHAT; this module does it, one
category at a time, and records it:

1. the plan is built and the chosen categories picked from it (`all` is every category);
2. each item is judged: an item an agent may apply is applied; one that needs the operator
   (an operator-set knob, a hand-edited file, a repair only the operator decides) is
   REFUSED unless its key was passed in ``confirm`` with a reason; a note (a new opt-in
   feature, a new knob) is acknowledged and writes nothing;
3. before ANY file is rewritten, every file the applied items will rewrite is copied to
   `.ddflow/backups/<stamp>-<from>-to-<to>/` (local, git-ignored) with a `manifest.json`
   saying what was there, so a half-done apply leaves the originals and a plan that can be
   run again (the event log is append-only: repairs and the upgrade record add lines, so
   there is no original of it to keep);
4. one `upgrade.applied` event (from, to, categories, backup, the items, who confirmed what)
   is appended, so replay explains the upgrade.

An apply is idempotent: a second one finds an empty plan and writes nothing, no backup and
no event. The project's version moves to the running one only when every item of the two
categories the manifest drives (`config`, `features`) was applied, acknowledged or
confirmed; a partial apply leaves the rest in the plan. A changed default for a knob the
project never set takes effect by itself: applying it only records and lists it, with the
command that pins the old behaviour.
"""

from __future__ import annotations

import contextlib
import re
import tomllib
from collections.abc import Callable, Collection, Mapping
from pathlib import Path
from typing import Any

from ..config import Config
from ..config_sections.upgrade import UPGRADE_BACKUPS
from ..core.events import UPGRADE_APPLIED_KIND
from ..core.model import State
from ..infra import tomlcfg as TC
from ..infra.fsio import replace_text
from . import adopt as AD
from . import claudehooks as CH
from . import configwrite as CW
from . import enforce as E
from . import migrations as MG
from . import repairs as RP
from . import upgrade_plan as UP
from .backups import (  # noqa: F401 -- the backups' home
    BACKUPS,
    MANIFEST,
    SnapshotRefused,
    backup_name,
    make_backup,
    make_snapshot,
    prune,
)

BACKUP_MODES = UPGRADE_BACKUPS
#: What `[upgrade].config_changes` accepts: who may apply a config change on a knob nobody set.
CONFIG_POLICIES = ("agent", "ask", "operator")

#: The categories whose items the manifest drives: they alone decide whether the project's
#: version moves up to the running one.
_VERSIONED = ("config", "features")

#: What a partial apply on a project with no version stamp records as the version it reached:
#: older than every release, so the manifest's changes are all still news (an empty `to`
#: would fall back to the stamp this very write adds, and the plan would call them done).
UNSTAMPED = "0.0.0"

APPLIED = "applied"
ACKNOWLEDGED = "acknowledged"
REFUSED = "refused"
FAILED = "failed"
UNAVAILABLE = "unavailable"
SKIPPED = "skipped"


def parse_categories(spec: str | Collection[str] | None) -> list[str]:
    """``all`` (or nothing) -> every category; else the named ones, in plan order.
    Raises ValueError for a name the plan has no category for."""
    if spec is None:
        return list(UP.CATEGORIES)
    names = [p.strip() for p in spec.split(",")] if isinstance(spec, str) else list(spec)
    names = [n for n in names if n]
    if not names or "all" in names:
        return list(UP.CATEGORIES)
    bad = [n for n in names if n not in UP.CATEGORIES]
    if bad:
        raise ValueError(
            f"unknown upgrade categor{'y' if len(bad) == 1 else 'ies'} {', '.join(bad)}; "
            f"known: {', '.join(UP.CATEGORIES)}, all"
        )
    return [c for c in UP.CATEGORIES if c in names]


# -- what an item touches ------------------------------------------------------------------


def _abs(repo: Path, p: str | Path) -> Path:
    p = Path(p)
    return p if p.is_absolute() else Path(repo) / p


def _config_files(repo: Path, cfg: Config, item: dict[str, Any]) -> list[Path]:
    src = cfg.sources.get(item["key"], "default")
    both = [CW.config_file(repo), CW.config_file(repo, local=True)]
    if item["change"] == "knob_removed":
        return both
    return [both[1] if src.startswith("local") else both[0]]


def _writes_config(item: dict[str, Any]) -> bool:
    """Does applying this config item change a file? Only a value somebody SET does: a knob
    at its shipped default takes the new default by itself, even when the policy makes the
    operator confirm it, and writing it out would pin it as if they had chosen it."""
    return item["action"] == UP.OPERATOR and bool(item.get("set_by"))


def touched(repo: Path, cfg: Config, item: dict[str, Any]) -> list[Path]:
    """The files applying ``item`` may change (the ones a backup must hold)."""
    cat = item["category"]
    if cat == "config":
        return _config_files(repo, cfg, item) if _writes_config(item) else []
    if cat == "instructions":
        return [_abs(repo, item["path"])]
    out = [_abs(repo, p) for p in item.get("paths", [])]
    if cat == "hooks" and item["id"].startswith("hooks:dangling:") and item.get("paths"):
        # `hooks install` refreshes both git hooks, so both are saved.
        try:
            out += [E.hooks_dir(repo) / name for name in E._hooks()]
        except RuntimeError:
            pass
    return out


# -- appliers ----------------------------------------------------------------------------------


def _remove_key(path: Path, key: str) -> bool:
    """Delete ``<section>.<knob>`` from the TOML file at ``path``, comments and layout kept.
    True when it was there. The result is parsed before it is written."""
    if not path.is_file():
        return False
    with TC.locked(path):
        text, was = TC.remove(path.read_text("utf-8"), key)
        if was:
            tomllib.loads(text)
            replace_text(path, text)
    return was


def _value(value: Any) -> object:
    """A plan item's new value as `apply_edit` takes it. A string is the TOML literal it
    spells (a plan stores `"true"` or `"[1, 2]"` as text), any other value is typed."""
    return CW.Spelled(value) if isinstance(value, str) else value


def _apply_config(repo: Path, cfg: Config, item: dict[str, Any], agent: str) -> tuple[str, str]:
    key, kind = item["key"], item["change"]
    src = cfg.sources.get(key, "default")
    if not _writes_config(item):
        return ACKNOWLEDGED, (
            f"{key}: the new default applies on its own"
            + (
                f"; pin the old behaviour with `ddflow config --set {key} {_short(item['old'])}`"
                if kind == "knob_changed"
                else ""
            )
        )
    if src.startswith("env"):
        return REFUSED, f"{key} is set by the environment ({src}); change it there"
    if kind == "knob_removed":
        gone = [p for p in _config_files(repo, cfg, item) if _remove_key(p, key)]
        return APPLIED, f"removed {key} from " + (
            ", ".join(p.name for p in gone) if gone else "no file (it was not there)"
        )
    local = src.startswith("local")
    res = CW.apply_edit(
        repo,
        CW.SetPairs([(key, _value(item["new"]))]),
        layer="local" if local else "file",
        agent=agent,
    )
    if res.error:
        return FAILED, f"{key}: {res.error}"
    return APPLIED, f"set {key} = {_short(item['new'])} (was {_short(item['old'])}, {src})"


def _short(v: Any) -> str:
    return UP._short(v)


def _apply_hook(repo: Path, item: dict[str, Any]) -> tuple[str, str]:
    iid = item["id"]
    if iid.startswith("hooks:missing:"):
        _, _, agent, name = iid.split(":", 3)
        try:
            msg = CH.install_spec(repo, CH.spec(agent, name))
        except CH.NewerSettings as exc:  # a newer ddflow's entry: upgrade, not a failure
            return REFUSED, str(exc)
        except (CH.SettingsError, KeyError) as exc:
            return FAILED, str(exc)
        return APPLIED, msg
    paths = item.get("paths") or []
    rel = paths[0] if paths else ""
    if item["fix"].startswith("ddflow hooks install --claude"):
        done: list[str] = []
        for h in CH.HOOKS:
            if h.file != rel:
                continue
            try:
                if CH.state_spec(repo, h)[0]:
                    done.append(CH.install_spec(repo, h))
            except CH.NewerSettings as exc:
                return REFUSED, str(exc)
            except CH.SettingsError as exc:
                return FAILED, str(exc)
        return (APPLIED, "; ".join(done)) if done else (FAILED, f"no ddflow hook found in {rel}")
    msg = E.install(repo)
    if E.NEWER_HINT in msg:  # the commit-msg hook's too: install() relabels it NOT INSTALLED
        return REFUSED, msg
    if "REFUSED" in msg or "NOT INSTALLED" in msg:
        return FAILED, msg
    return APPLIED, msg


def _apply_mcp(repo: Path, item: dict[str, Any]) -> tuple[str, str]:
    paths = item.get("paths") or []
    rel = paths[0] if paths else ""
    keys = [k for k, t in AD.AGENT_TARGETS.items() if t.config == rel]
    if not keys:
        return FAILED, f"no adopted agent writes {rel or 'this MCP entry'}"
    msg = AD._register_mcp(repo, keys[0])
    if isinstance(msg, AD.Refused):
        return FAILED, str(msg)
    return APPLIED, msg


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
) -> dict[str, Any]:
    """Apply the chosen categories of the plan (see the module docstring).

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
    confirm = dict(confirm or {})
    plan = plan or UP.build(repo, log, cfg, st)
    frm, to = plan["project_version"], plan["running"]
    results, todo = _select(plan, parse_categories(categories), confirm, config_changes)

    files = [p for i in todo for p in touched(repo, cfg, i)]
    backup_dir, why, refused = _save(repo, cfg, backup, files, frm, to)
    if why:
        # Nothing was changed: the originals could not be saved, so no item is applied.
        status = REFUSED if refused else FAILED
        failed = results + [_rec(i, status, why) for i in todo]
        return _finish(failed, "", frm, frm or UNSTAMPED, [], {})

    saved = {p.resolve() for p in files} if backup_dir else set()

    def late_backup(more: list[Path]) -> str:
        """Files a migration's own detect now plans that the plan's did not name (earlier
        steps of this run can change what it finds): saved too, in the same mode."""
        fresh = [p for p in more if p.resolve() not in saved]
        if not fresh or backup == "none":
            return ""
        where, why, _refused = _save(repo, cfg, backup, fresh, frm, to)
        if why:
            raise OSError(why)
        saved.update(p.resolve() for p in fresh)
        return where

    results += _execute(repo, log, cfg, todo, agent, late_backup)
    ok, done_cats, reasons = _summarise(results, todo, confirm)
    new_to = (frm or UNSTAMPED) if _unresolved(plan, ok) else to
    if ok:
        _record(log, todo, ok, frm, new_to, done_cats, backup_dir, reasons)
    return _finish(results, backup_dir, frm, new_to, done_cats, reasons)


def _summarise(
    results: list[dict[str, Any]], todo: list[dict[str, Any]], confirm: Mapping[str, str]
) -> tuple[set[str], list[str], dict[str, str]]:
    """`(ids settled, categories with something settled, the operator's reasons)`."""
    ok = {r["id"] for r in results if r["status"] in (APPLIED, ACKNOWLEDGED)}
    cats = [c for c in UP.CATEGORIES if any(r["category"] == c and r["id"] in ok for r in results)]
    reasons = {_ident(i): why for i in todo if i["id"] in ok and (why := _confirmed(i, confirm))}
    return ok, cats, reasons


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
            snap = make_snapshot(repo, files, frm, to)
            extra = f" (and {snap.local} for what git cannot hold)" if snap.local else ""
            where = snap.tag + extra
        else:
            where = str(make_backup(repo, files, frm, to))
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
) -> None:
    """Append the one `upgrade.applied` event: replay's account of this upgrade."""
    log.append(
        UPGRADE_APPLIED_KIND,
        "upgrade",
        {
            "from": frm,
            "to": new_to,
            "categories": cats,
            "backup": backup_dir,
            "items": sorted(ok),
            "confirmed": reasons,
            "config_changes": [
                {k: i[k] for k in ("key", "change", "old", "new")}
                for i in todo
                if i["category"] == "config" and i["id"] in ok
            ],
            "summary": f"upgrade {frm or 'unstamped'} -> {new_to}: {', '.join(cats)}",
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
                results.append(_rec(item, SKIPPED, item["summary"]))
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
) -> list[dict[str, Any]]:
    """Run each item's applier; repairs and instruction files go in one batch each."""
    out: list[dict[str, Any]] = []
    for item in todo:
        if item["category"] not in ("repairs", "migrations", "instructions"):
            out.append(_rec(item, *_run(repo, cfg, item, agent)))
    repairs = [i for i in todo if i["category"] == "repairs"]
    migrations = [i for i in todo if i["category"] == "migrations"]
    docs = [i for i in todo if i["category"] == "instructions"]
    if repairs:
        out += _run_repairs(repo, log, cfg, repairs)
    if migrations:
        out += _run_migrations(repo, log, cfg, migrations, late_backup)
    if docs:
        out += _run_instructions(repo, docs)
    return out


def _refusal(item: dict[str, Any], config_changes: str) -> str:
    key = _ident(item)
    why = "needs the operator's confirmation"
    if item["category"] == "config" and item["action"] == UP.AGENT:
        why += f" ([upgrade].config_changes = {config_changes})"
    return f"{why}: ddflow upgrade --apply --confirm {key} --reason '<why>'"


def _run(repo: Path, cfg: Config, item: dict[str, Any], agent: str) -> tuple[str, str]:
    cat = item["category"]
    try:
        if cat == "config":
            return _apply_config(repo, cfg, item, agent)
        if cat == "features":
            return ACKNOWLEDGED, item["summary"] + (f" -> {item['fix']}" if item.get("fix") else "")
        if cat == "hooks":
            return _apply_hook(repo, item)
        if cat == "mcp":
            return _apply_mcp(repo, item)
    except (OSError, ValueError, RuntimeError) as exc:
        return FAILED, f"{type(exc).__name__}: {exc}"
    return SKIPPED, f"no applier for {cat}"


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
