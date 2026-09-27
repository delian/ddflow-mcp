"""The Claude Code SessionStart hook: the brief arrives without anyone asking for it.

Everything else in ddflow's session start is a REQUEST -- the MCP instructions say "call
ddflow_brief first", the rules file says it again -- and a request is exactly what a
model skips after a context compaction. The source projects learned this the hard way:
their OptMem store had to be read first every session, and the rule saying so was prose.
A SessionStart hook is the one mechanism Claude Code runs itself, and its stdout is added
to the session's context. So the brief, the operational memory and any crashed work
waiting for recovery reach the agent whether or not it remembers to ask.

This module only edits `.claude/settings.json`; what the hook PRINTS is
`api.setup.hooks(action="session-start")`.

Two properties, both load-bearing:

* **It never replaces a hook it does not own.** The projects this was built for already
  run SessionStart hooks (a worktree-sync check, a toolchain bootstrap). Ours is found by
  its command line, added beside them, and removed alone.
* **It refuses to touch a settings file it cannot parse.** Rewriting an invalid file
  would drop whatever the operator was halfway through writing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: What identifies OUR hook among the operator's: the subcommand it runs. Matched as a
#: substring of the command, because the interpreter path in front of it varies.
MARKER = "hooks session-start"
#: The events Claude Code fires SessionStart for. `compact` matters most: it is the
#: moment the model loses the rules it was given at the start.
MATCHER = "startup|resume|clear|compact"


def settings_path(repo: Path) -> Path:
    """The PROJECT settings file: committed, so every clone and worktree gets the hook."""
    return Path(repo) / ".claude" / "settings.json"


class SettingsError(ValueError):
    """The settings file exists and is not a JSON object; nothing was written."""


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise SettingsError(f"{path} is not valid JSON ({exc}); not touching it") from exc
    if not isinstance(data, dict):
        raise SettingsError(f"{path} is not a JSON object; not touching it")
    return data


def _ours(hook: Any) -> bool:
    return isinstance(hook, dict) and MARKER in str(hook.get("command", ""))


def _groups(data: dict[str, Any]) -> list[Any]:
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return []
    groups = hooks.get("SessionStart")
    return groups if isinstance(groups, list) else []


def installed(repo: Path) -> bool:
    try:
        data = _read(settings_path(repo))
    except SettingsError:
        return False
    return any(
        _ours(h) for g in _groups(data) if isinstance(g, dict) for h in (g.get("hooks") or [])
    )


def install(repo: Path, command: str) -> str:
    """Add (or refresh) our SessionStart hook, leaving every other hook exactly as it was."""
    path = settings_path(repo)
    data = _read(path)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise SettingsError(f"{path}: `hooks` is not an object; not touching it")
    groups = hooks.setdefault("SessionStart", [])
    if not isinstance(groups, list):
        raise SettingsError(f"{path}: `hooks.SessionStart` is not a list; not touching it")
    entry = {"type": "command", "command": command}
    for g in groups:
        if not isinstance(g, dict):
            continue
        for i, h in enumerate(g.get("hooks") or []):
            if _ours(h):
                if h == entry:
                    return f"the ddflow SessionStart hook is already in {path}"
                g["hooks"][i] = entry
                _write(path, data)
                return f"updated the ddflow SessionStart hook in {path}"
    groups.append({"matcher": MATCHER, "hooks": [entry]})
    _write(path, data)
    return f"added a SessionStart hook to {path}: every session starts with the ddflow brief"


def uninstall(repo: Path) -> str:
    """Remove OUR hook only; drop a group only if ours was all it held."""
    path = settings_path(repo)
    if not path.exists():
        return f"no {path}; nothing to remove"
    data = _read(path)
    groups = _groups(data)
    removed = 0
    kept_groups = []
    for g in groups:
        if not isinstance(g, dict):
            kept_groups.append(g)
            continue
        hooks = g.get("hooks") or []
        rest = [h for h in hooks if not _ours(h)]
        removed += len(hooks) - len(rest)
        if rest or not hooks:
            kept_groups.append({**g, "hooks": rest} if hooks else g)
    if not removed:
        return f"no ddflow SessionStart hook in {path}"
    if kept_groups:
        data["hooks"]["SessionStart"] = kept_groups
    else:
        del data["hooks"]["SessionStart"]
        if not data["hooks"]:
            del data["hooks"]
    _write(path, data)
    return f"removed the ddflow SessionStart hook from {path}"


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", "utf-8")
