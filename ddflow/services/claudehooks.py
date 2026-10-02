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
#: The prompt-capture hook: runs `ddflow hooks prompt` with the harness's JSON on stdin.
PROMPT_MARKER = "hooks prompt"
#: Claude Code's event for "the user submitted a prompt", and Gemini CLI's equivalent.
PROMPT_EVENT = "UserPromptSubmit"
GEMINI_PROMPT_EVENT = "BeforeAgent"
GEMINI_SETTINGS = ".gemini/settings.json"


def settings_path(repo: Path, rel: str = ".claude/settings.json") -> Path:
    """The PROJECT settings file: committed, so every clone and worktree gets the hook."""
    return Path(repo) / rel


class SettingsError(ValueError):
    """The settings file exists and is not a JSON object; nothing was written."""


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise SettingsError(f"{path} is not valid JSON ({exc}); not touching it") from exc
    except (UnicodeDecodeError, OSError) as exc:
        # Not a JSONDecodeError, so it escaped every handler and crashed `hooks status`
        # on a file ddflow never wrote (roborev 826).
        raise SettingsError(f"{path} could not be read ({exc}); not touching it") from exc
    if not isinstance(data, dict):
        raise SettingsError(f"{path} is not a JSON object; not touching it")
    return data


def _hooks_of(group: Any, path: Path) -> list[Any]:
    """A SessionStart group's hook list; a non-list is refused, not iterated.

    `{"hooks": 5}` in a group -- another tool's bug, a manual edit -- crashed status,
    install and uninstall with a TypeError instead of the refusal this module promises
    for a file it cannot make sense of (rubber-duck).
    """
    if not isinstance(group, dict):
        return []
    hooks = group.get("hooks")
    if hooks is None:
        return []
    if not isinstance(hooks, list):
        raise SettingsError(
            f"{path}: a SessionStart group's `hooks` is not a list; not touching it"
        )
    return hooks


def _ours(hook: Any, marker: str = MARKER) -> bool:
    return isinstance(hook, dict) and marker in str(hook.get("command", ""))


def _groups(data: dict[str, Any], event: str = "SessionStart") -> list[Any]:
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return []
    groups = hooks.get(event)
    return groups if isinstance(groups, list) else []


def installed(repo: Path, **kw: Any) -> bool:
    """True only when our hook is demonstrably there; see `state` for "could not tell"."""
    return state(repo, **kw)[0] is True


def state(
    repo: Path,
    *,
    event: str = "SessionStart",
    marker: str = MARKER,
    rel: str = ".claude/settings.json",
) -> tuple[bool | None, str]:
    """(installed?, why). None means the settings file could not be read -- which is
    NOT the same as "not installed", and reporting it as that told an operator to
    install a hook into a file ddflow would then refuse to touch (roborev 826)."""
    try:
        data = _read(settings_path(repo, rel))
    except SettingsError as exc:
        return None, str(exc)
    path = settings_path(repo, rel)
    try:
        on = any(_ours(h, marker) for g in _groups(data, event) for h in _hooks_of(g, path))
    except SettingsError as exc:
        return None, str(exc)
    return on, ""


def install(
    repo: Path,
    command: str,
    *,
    event: str = "SessionStart",
    marker: str = MARKER,
    matcher: str | None = MATCHER,
    rel: str = ".claude/settings.json",
) -> str:
    """Add (or refresh) our hook for `event`, leaving every other hook exactly as it was."""
    path = settings_path(repo, rel)
    data = _read(path)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise SettingsError(f"{path}: `hooks` is not an object; not touching it")
    groups = hooks.setdefault(event, [])
    if not isinstance(groups, list):
        raise SettingsError(f"{path}: `hooks.{event}` is not a list; not touching it")
    entry = {"type": "command", "command": command}
    for g in groups:
        for i, h in enumerate(_hooks_of(g, path)):
            if _ours(h, marker):
                if h == entry:
                    return f"the ddflow {event} hook is already in {path}"
                g["hooks"][i] = entry
                _write(path, data)
                return f"updated the ddflow {event} hook in {path}"
    groups.append({"matcher": matcher, "hooks": [entry]} if matcher else {"hooks": [entry]})
    _write(path, data)
    if event == "SessionStart":
        return f"added a SessionStart hook to {path}: every session starts with the ddflow brief"
    return f"added a {event} hook to {path}: every operator prompt is recorded"


def uninstall(
    repo: Path,
    *,
    event: str = "SessionStart",
    marker: str = MARKER,
    rel: str = ".claude/settings.json",
) -> str:
    """Remove OUR hook only; drop a group only if ours was all it held."""
    path = settings_path(repo, rel)
    if not path.exists():
        return f"no {path}; nothing to remove"
    data = _read(path)
    groups = _groups(data, event)
    removed = 0
    kept_groups = []
    for g in groups:
        if not isinstance(g, dict):
            kept_groups.append(g)
            continue
        hooks = _hooks_of(g, path)
        rest = [h for h in hooks if not _ours(h, marker)]
        removed += len(hooks) - len(rest)
        if rest or not hooks:
            kept_groups.append({**g, "hooks": rest} if hooks else g)
    if not removed:
        return f"no ddflow {event} hook in {path}"
    if kept_groups:
        data["hooks"][event] = kept_groups
    else:
        del data["hooks"][event]
        if not data["hooks"]:
            del data["hooks"]
    _write(path, data)
    return f"removed the ddflow {event} hook from {path}"


def _write(path: Path, data: dict[str, Any]) -> None:
    """Atomically: a truncate-then-write interrupted midway leaves an EMPTY settings
    file, which `_read` takes as `{}` -- the operator's permissions and hooks gone, and
    the next install reporting success over the loss (roborev 826)."""
    from ..infra.tomlcfg import atomic_write

    atomic_write(path, json.dumps(data, indent=2) + "\n")
