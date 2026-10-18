"""The ``flat-list`` hook layout: ``{"version": 1, "hooks": {event: [{<key>: command, ...}]}}``.

Cursor (``.cursor/hooks.json``, entries ``{"command": ...}``) and Copilot (a file under
``.github/hooks/``, entries ``{"type": "command", "bash": ...}``) list their hook commands
straight under the event, with no matcher group around them (the layouts their descriptors'
``[verified] sources`` document). Same rules as the nested writer: our entry is found by its
stamped region, added beside the operator's, refreshed in place and removed alone; a file
that does not parse is never rewritten.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...infra.fsio import NewerContent, RegionError
from .. import claudehooks as CH

#: Descriptor style -> (the key holding the command line, constant keys of every entry).
LAYOUTS: dict[str, tuple[str, dict[str, str]]] = {
    "cursor": ("command", {}),
    "copilot": ("bash", {"type": "command"}),
}


def _entries(data: dict[str, Any], event: str, path: Path) -> list[Any]:
    hooks = data.get("hooks")
    if hooks is None:
        return []
    if not isinstance(hooks, dict) or not isinstance(hooks.get(event, []), list):
        raise CH.SettingsError(f"{path}: `hooks.{event}` is not a list; not touching it")
    return hooks.get(event, [])


def _mine(entry: Any, key: str, marker: str) -> bool:
    return isinstance(entry, dict) and CH._ours({"command": str(entry.get(key, ""))}, marker)


def install(repo: Path, h: CH.HookSpec, style: str) -> str:
    key, const = LAYOUTS[style]
    path = CH.settings_path(repo, h.file)
    data = CH._read(path)
    items = _entries(data, h.event, path)
    region = CH._region(h.marker)
    command = CH.command(h)
    for entry in items:
        if _mine(entry, key, h.marker):
            have = str(entry[key])
            try:
                wanted = (
                    region.splice(have, command) if region.owns(have) else region.render(command)
                )
            except NewerContent as exc:
                raise CH.NewerSettings(f"{path}: the ddflow {h.event} hook: {exc}") from exc
            except RegionError as exc:
                raise CH.SettingsError(f"{path}: the ddflow {h.event} hook: {exc}") from exc
            if wanted == have:
                return f"the ddflow {h.event} hook is already in {path}"
            entry[key] = wanted
            CH._write(path, data)
            return f"updated the ddflow {h.event} hook in {path}"
    data.setdefault("version", 1)
    if data.get("hooks") is None:  # absent or null: both mean no hooks yet
        data["hooks"] = {}
    hooks = data["hooks"]
    hooks.setdefault(h.event, []).append({**const, key: region.render(command)})
    CH._write(path, data)
    return f"added a {h.event} hook to {path}: {h.purpose}"


def uninstall(repo: Path, h: CH.HookSpec, style: str) -> str:
    key, _const = LAYOUTS[style]
    path = CH.settings_path(repo, h.file)
    if not path.exists():
        return f"no {path}; nothing to remove"
    data = CH._read(path)
    items = _entries(data, h.event, path)
    rest = [e for e in items if not _mine(e, key, h.marker)]
    if len(rest) == len(items):
        return f"no ddflow {h.event} hook in {path}"
    if rest:
        data["hooks"][h.event] = rest
    else:
        del data["hooks"][h.event]
        if not data["hooks"]:
            del data["hooks"]
    CH._write(path, data)
    return f"removed the ddflow {h.event} hook from {path}"


def state(repo: Path, h: CH.HookSpec, style: str) -> tuple[bool | None, str]:
    key, _const = LAYOUTS[style]
    path = CH.settings_path(repo, h.file)
    try:
        items = _entries(CH._read(path), h.event, path)
    except CH.SettingsError as exc:
        return None, str(exc)
    return any(_mine(e, key, h.marker) for e in items), ""
