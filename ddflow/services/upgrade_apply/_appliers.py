"""The appliers of the `ddflow upgrade --apply` step: what an item touches, and how one
item of each category is applied.

Split out of ``ddflow/services/upgrade_apply.py``: the plan (`services.upgrade_plan`)
says WHAT; these `_apply_*` / `_run` helpers do one item, and `_apply.apply` runs them in
order. The status words come from `._constants`.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Collection
from pathlib import Path
from typing import Any

from ...config import Config
from ...infra import tomlcfg as TC
from ...infra.fsio import replace_text
from .. import adopt as AD
from .. import claudehooks as CH
from .. import configwrite as CW
from .. import enforce as E
from .. import upgrade_plan as UP
from ._constants import ACKNOWLEDGED, APPLIED, FAILED, REFUSED, SKIPPED


def parse_categories(spec: str | Collection[str] | None) -> list[str]:
    """``all`` (or nothing) -> every category; else the named ones, in plan order.
    Raises ValueError for a name the plan has no category for."""
    if spec is None:
        return list(UP.CATEGORIES)
    names = [p.strip() for p in spec.split(",")] if isinstance(spec, str) else list(spec)
    names = [n for n in names if n]
    if not names:
        return list(UP.CATEGORIES)
    bad = [n for n in names if n != "all" and n not in UP.CATEGORIES]
    if bad:
        raise ValueError(
            f"unknown upgrade categor{'y' if len(bad) == 1 else 'ies'} {', '.join(bad)}; "
            f"known: {', '.join(UP.CATEGORIES)}, all"
        )
    if "all" in names:
        return list(UP.CATEGORIES)
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
    operator confirm it, and writing it out would pin it as if they had chosen it. Nor does a
    CHANGED default for a knob the project pins: confirming accepts the change and the
    operator's pin wins, so only a knob removed from the schema is rewritten (deleted)."""
    return (
        item["action"] == UP.OPERATOR
        and bool(item.get("set_by"))
        and item["change"] == "knob_removed"
    )


def touched(repo: Path, cfg: Config, item: dict[str, Any]) -> list[Path]:
    """The files applying ``item`` may change (the ones a backup must hold)."""
    cat = item["category"]
    if cat == "config":
        return _config_files(repo, cfg, item) if _writes_config(item) else []
    if cat == "instructions":
        doc = _abs(repo, item["path"])
        # A refresh of a hand-edited document keeps the edit in a NEW `.local-edits[.N]` file
        # beside it (the first free name, as the writer will take it): a file the apply creates,
        # so the backup must name it for a restore to remove it again.
        return [doc, AD.local_edits_path(doc)]
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
    pinned = bool(item.get("set_by"))
    if pinned and src.startswith("env") and item["action"] == UP.OPERATOR:
        return REFUSED, f"{key} is set by the environment ({src}); change it there"
    if kind == "knob_removed" and _writes_config(item):
        gone = [p for p in _config_files(repo, cfg, item) if _remove_key(p, key)]
        return APPLIED, f"removed {key} from " + (
            ", ".join(p.name for p in gone) if gone else "no file (it was not there)"
        )
    if pinned and kind == "knob_changed":
        # confirming accepts the new default; the operator's pin wins, so nothing is written
        return ACKNOWLEDGED, (
            f"{key}: default changed {_short(item['old'])} -> {_short(item['new'])}; "
            f"kept this project's value {_short(UP._current(cfg, key))} ({src}); "
            f"to adopt the new default: ddflow config --set {key} {json.dumps(item['new'])}"
        )
    return ACKNOWLEDGED, (
        f"{key}: the new default applies on its own"
        + (
            f"; pin the old behaviour with `ddflow config --set {key} {json.dumps(item['old'])}`"
            if kind == "knob_changed"
            else ""
        )
    )


def _short(v: Any) -> str:
    return UP._short(v)


def _apply_hook(repo: Path, item: dict[str, Any]) -> tuple[str, str]:
    iid = item["id"]
    if iid.startswith(("hooks:missing:", "hooks:stale:")):
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
