"""`ddflow upgrade --apply`: do what `upgrade --plan` lists, with a backup first.

Decisions D-upgrade-model (3), D-upgrade-backups and D-upgrade-config-changes, task
B-upgrade.4-apply. The plan (`services.upgrade_plan`) says WHAT; this module does it, one
category at a time, and records it:

1. the plan is built and the chosen categories picked from it (`all` is every category);
2. each item is judged: an item an agent may apply is applied; one that needs the operator
   (an operator-set knob, a hand-edited file, a repair only the operator decides) is
   REFUSED unless its key was passed in ``confirm`` with a reason; a note (a new opt-in
   feature, a new knob) is acknowledged and writes nothing;
3. before ANY write, every file the applied items will touch is copied to
   `.ddflow/backups/<stamp>-<from>-to-<to>/` (local, git-ignored) with a `manifest.json`
   saying what was there, so a half-done apply leaves the originals and a plan that can be
   run again;
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

import json
import shutil
import tomllib
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

from ..config import Config
from ..core import clock
from ..core.events import UPGRADE_APPLIED_KIND
from ..core.model import State
from ..infra import tomlcfg as TC
from ..infra.fsio import ensure_ignored_dir, replace_text
from . import adopt as AD
from . import claudehooks as CH
from . import configwrite as CW
from . import enforce as E
from . import repairs as RP
from . import upgrade_plan as UP

BACKUPS = ".ddflow/backups"
MANIFEST = "manifest.json"

BACKUP_MODES = ("local", "none")
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


def touched(repo: Path, cfg: Config, item: dict[str, Any]) -> list[Path]:
    """The files applying ``item`` may change (the ones a backup must hold)."""
    cat = item["category"]
    if cat == "config":
        needs_write = item["action"] == UP.OPERATOR
        return _config_files(repo, cfg, item) if needs_write else []
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


# -- backup ----------------------------------------------------------------------------------


def backup_name(frm: str, to: str) -> str:
    return f"{clock.compact_at()}-{frm or 'unstamped'}-to-{to}"


def make_backup(repo: Path, files: Collection[Path], frm: str, to: str) -> Path:
    """Copy every file that exists to `.ddflow/backups/<stamp>-<from>-to-<to>/`, a file inside
    the project at its relative path and one outside it under `_outside/`, and write a
    `manifest.json` listing each file, whether it existed and where its copy is. Returns the
    backup directory. Raises OSError when it cannot be written: the caller then writes
    nothing."""
    repo = Path(repo).resolve()
    root = ensure_ignored_dir(
        repo / BACKUPS, comment="ddflow upgrade backups: local, not shared, never committed"
    )
    dest = root / backup_name(frm, to)
    dest.mkdir(parents=True)
    entries: list[dict[str, Any]] = []
    for f in dict.fromkeys(Path(x).resolve() for x in files):
        try:
            rel = f.relative_to(repo)
            stored = rel.as_posix()
            shown = stored
        except ValueError:
            stored = "_outside/" + f.as_posix().lstrip("/")
            shown = f.as_posix()
        existed = f.is_file()
        if existed:
            target = dest / stored
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
        entries.append({"path": shown, "stored": stored if existed else "", "existed": existed})
    manifest = {"from": frm, "to": to, "mode": "local", "files": entries}
    replace_text(dest / MANIFEST, json.dumps(manifest, indent=2) + "\n")
    return dest


# -- appliers ----------------------------------------------------------------------------------


def _remove_key(path: Path, key: str) -> bool:
    """Delete ``<section>.<knob>`` from the TOML file at ``path``, comments and layout kept.
    True when it was there. The result is parsed before it is written."""
    import tomlkit

    if not path.is_file():
        return False
    section, _, knob = key.rpartition(".")
    with TC.locked(path):
        doc = tomlkit.parse(path.read_text("utf-8"))
        table: Any = doc
        for part in section.split(".") if section else []:
            table = table.get(part) if isinstance(table, dict) else None
            if table is None:
                return False
        if not isinstance(table, dict) or knob not in table:
            return False
        del table[knob]
        text = tomlkit.dumps(doc)
        tomllib.loads(text)
        replace_text(path, text)
    return True


def _value_text(value: Any) -> str:
    return value if isinstance(value, str) else TC.value(value)


def _apply_config(repo: Path, cfg: Config, item: dict[str, Any], agent: str) -> tuple[str, str]:
    key, kind = item["key"], item["change"]
    src = cfg.sources.get(key, "default")
    if item["action"] != UP.OPERATOR:
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
    err, _text = CW._write_config(repo, [(key, _value_text(item["new"]))], local=local, agent=agent)
    if err:
        return FAILED, f"{key}: {err}"
    return APPLIED, f"set {key} = {_short(item['new'])} (was {_short(item['old'])}, {src})"


def _short(v: Any) -> str:
    return UP._short(v)


def _apply_hook(repo: Path, item: dict[str, Any]) -> tuple[str, str]:
    iid = item["id"]
    if iid.startswith("hooks:missing:"):
        _, _, agent, name = iid.split(":", 3)
        try:
            msg = CH.install_spec(repo, CH.spec(agent, name))
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
            except CH.SettingsError as exc:
                return FAILED, str(exc)
        return (APPLIED, "; ".join(done)) if done else (FAILED, f"no ddflow hook found in {rel}")
    msg = E.install(repo)
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
    for k in (item["id"], item.get("key"), item.get("repair"), item.get("path")):
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
    config_changes: str = "agent",
    backup: str = "local",
    agent: str = "",
    plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply the chosen categories of the plan (see the module docstring).

    Returns plain data: ``results`` (one record per item: id, category, status, detail),
    ``refused``/``failed`` counts, ``backup`` (the directory, "" when nothing was written),
    ``from``/``to``, ``noop`` and the ``text`` for a person. ``exit`` is 0 when everything
    chosen was applied, acknowledged or already fine, 1 when an applier failed, 3 when an
    item waits for the operator's confirmation. Raises ValueError for an unknown category,
    ``config_changes`` or ``backup`` value."""
    repo = Path(repo)
    if backup not in BACKUP_MODES:
        raise ValueError(f"unknown backup mode {backup!r}; known: {', '.join(BACKUP_MODES)}")
    if config_changes not in CONFIG_POLICIES:
        raise ValueError(
            f"unknown config_changes policy {config_changes!r}; known: {', '.join(CONFIG_POLICIES)}"
        )
    confirm = dict(confirm or {})
    plan = plan or UP.build(repo, log, cfg, st)
    frm, to = plan["project_version"], plan["running"]
    results, todo = _select(plan, parse_categories(categories), confirm, config_changes)

    files = [p for i in todo for p in touched(repo, cfg, i)]
    backup_dir = ""
    if files and backup == "local":
        try:
            backup_dir = str(make_backup(repo, files, frm, to))
        except OSError as exc:
            detail = f"no backup could be written ({exc}); nothing was changed"
            return _finish(results + [_rec(i, FAILED, detail) for i in todo], "", frm, to, [], {})

    results += _execute(repo, log, cfg, todo, agent)
    ok = {r["id"] for r in results if r["status"] in (APPLIED, ACKNOWLEDGED)}
    done_cats = [
        c for c in UP.CATEGORIES if any(r["category"] == c and r["id"] in ok for r in results)
    ]
    reasons = {_ident(i): why for i in todo if i["id"] in ok and (why := _confirmed(i, confirm))}
    new_to = (frm or UNSTAMPED) if _unresolved(plan, ok) else to
    if ok:
        log.append(
            UPGRADE_APPLIED_KIND,
            "upgrade",
            {
                "from": frm,
                "to": new_to,
                "categories": done_cats,
                "backup": backup_dir,
                "items": sorted(ok),
                "confirmed": reasons,
                "config_changes": [
                    {k: i[k] for k in ("key", "change", "old", "new")}
                    for i in todo
                    if i["category"] == "config" and i["id"] in ok
                ],
                "summary": f"upgrade {frm or 'unstamped'} -> {to}: {', '.join(done_cats)}",
            },
        )
    return _finish(results, backup_dir, frm, new_to, done_cats, reasons)


def _ident(item: dict[str, Any]) -> str:
    return item.get("key") or item.get("repair") or item.get("path") or item["id"]


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
    repo: Path, log: Any, cfg: Config, todo: list[dict[str, Any]], agent: str
) -> list[dict[str, Any]]:
    """Run each item's applier; repairs and instruction files go in one batch each."""
    out: list[dict[str, Any]] = []
    for item in todo:
        if item["category"] not in ("repairs", "instructions"):
            out.append(_rec(item, *_run(repo, cfg, item, agent)))
    repairs = [i for i in todo if i["category"] == "repairs"]
    docs = [i for i in todo if i["category"] == "instructions"]
    if repairs:
        out += _run_repairs(repo, log, cfg, repairs)
    if docs:
        out += _run_instructions(repo, docs)
    return out


def _refusal(item: dict[str, Any], config_changes: str) -> str:
    key = item.get("key") or item.get("repair") or item.get("path") or item["id"]
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


def _run_instructions(repo: Path, items: list[dict[str, Any]]) -> list[dict]:
    paths = [i["path"] for i in items]
    try:
        actions = AD.refresh_docs(repo, only=paths)
    except (OSError, ValueError) as exc:
        return [_rec(i, FAILED, f"{type(exc).__name__}: {exc}") for i in items]
    return [
        _rec(i, APPLIED, "; ".join(a for a in actions if i["path"] in a) or "refreshed")
        for i in items
    ]


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
    counts = {s: sum(1 for r in results if r["status"] == s) for s in (REFUSED, FAILED)}
    changed = [r for r in results if r["status"] in (APPLIED, ACKNOWLEDGED)]
    exit_code = 1 if counts[FAILED] else (3 if counts[REFUSED] else 0)
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
        "backup": backup_dir,
        "confirmed": reasons,
        "noop": not results,
        "exit": exit_code,
        "text": "\n".join(lines),
    }
