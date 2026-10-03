"""Launchers that point at something that is gone.

A git hook, a Claude Code hook command and an MCP client's `ddflow` entry each RECORD
where ddflow was when they were written: an absolute script path, or an interpreter and
the directory holding the package. Delete that venv, uninstall that tool, remove that
worktree and the record dangles. The git hooks and Claude commands now fall back to
`ddflow` on PATH and then fail open (`enforce.command_line`), which keeps a commit from
being blocked -- and is exactly why this module exists: a check that quietly stopped
running must still be REPORTED, by `ddflow doctor` and `ddflow hooks status`, with the
command that refreshes it.

Nothing here writes anything; it reads the recorded paths back out of the lines
`enforce.command_line` wrote (and out of the older lines that had no fallback).
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Written by `command_line`: the probe that decides whether the recorded launcher runs.
_PROBE = re.compile(r'\[ -[xf] "([^"]+)" \]')
#: Lines written before the fallback existed: `exec "<script or python>"` and the
#: package directory on PYTHONPATH.
_LEGACY_EXEC = re.compile(r'exec "([^"]+)"')
_LEGACY_PYPATH = re.compile(r'PYTHONPATH="([^"$]+)')


@dataclass(frozen=True)
class Dangling:
    where: str  #: what holds the record: a hook file, a settings file, an MCP config
    missing: tuple[str, ...]  #: the recorded paths that no longer exist
    fallback: bool  #: True when `ddflow` on PATH still runs it
    fix: str  #: the command that refreshes it

    def render(self) -> str:
        gone = ", ".join(self.missing)
        if self.fallback:
            tail = "it falls back to `ddflow` on PATH for now"
        else:
            tail = "NOTHING runs it: the hook fails open and the check is skipped"
        return f"{self.where} records {gone}, which no longer exists; {tail} -- run `{self.fix}`"


def recorded_paths(command: str) -> list[str]:
    """The files a hook line needs, in the order it records them."""
    found = _PROBE.findall(command)
    if not found:
        found = _LEGACY_EXEC.findall(command)
        found += [f"{r}/ddflow/__init__.py" for r in _LEGACY_PYPATH.findall(command)]
    return list(dict.fromkeys(found))


def _gone(paths: list[str]) -> tuple[str, ...]:
    return tuple(p for p in paths if not os.path.exists(p))


def _on_path() -> bool:
    return shutil.which("ddflow") is not None


def check_command(where: str, command: str, fix: str) -> Dangling | None:
    """A hook command line whose recorded launcher is gone, or None."""
    missing = _gone(recorded_paths(command))
    # Only a line written WITH the fallback has one; an older line execs its dead path.
    return (
        Dangling(where, missing, _on_path() and bool(_PROBE.search(command)), fix)
        if missing
        else None
    )


def check_hook_file(path: Path, fix: str = "ddflow hooks install") -> Dangling | None:
    try:
        text = path.read_text("utf-8", errors="replace")
    except OSError:
        return None
    return check_command(f"the git hook {path}", text, fix)


def _commands(data: Any) -> list[str]:
    """Every hook command string in a harness settings file."""
    out: list[str] = []
    hooks = data.get("hooks") if isinstance(data, dict) else None
    for groups in (hooks or {}).values() if isinstance(hooks, dict) else ():
        for g in groups if isinstance(groups, list) else ():
            for h in (
                g.get("hooks", [])
                if isinstance(g, dict) and isinstance(g.get("hooks"), list)
                else ()
            ):
                if isinstance(h, dict) and isinstance(h.get("command"), str):
                    out.append(h["command"])
    return out


def check_settings(repo: Path) -> list[Dangling]:
    """ddflow's own hook commands in `.claude/settings.json` and `.gemini/settings.json`."""
    from .claudehooks import GEMINI_SETTINGS, MARKER, PROMPT_MARKER

    out: list[Dangling] = []
    for rel in (".claude/settings.json", GEMINI_SETTINGS):
        path = Path(repo) / rel
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        for cmd in _commands(data):
            if MARKER in cmd or PROMPT_MARKER in cmd:
                fix = "ddflow hooks install --claude" + (" --gemini" if "gemini" in rel else "")
                if d := check_command(f"the hook command in {rel}", cmd, fix):
                    out.append(d)
    return out


def _entry_parts(entry: Any) -> tuple[str, dict[str, str]]:
    """(command, env) from an MCP server entry in any agent's shape."""
    if not isinstance(entry, dict):
        return "", {}
    cmd = entry.get("command")
    if isinstance(cmd, list):  # opencode: one array holding the arguments too
        cmd = cmd[0] if cmd and isinstance(cmd[0], str) else ""
    env = entry.get("env") or entry.get("environment") or {}
    return (cmd if isinstance(cmd, str) else ""), (env if isinstance(env, dict) else {})


def check_mcp(repo: Path) -> list[Dangling]:
    """The `ddflow` server entry of every agent config the project has."""
    from .adopt import AGENT_TARGETS, get_server

    out: list[Dangling] = []
    seen: set[str] = set()
    for target in AGENT_TARGETS.values():
        rel = target.config
        if not rel or rel in seen:
            continue
        seen.add(rel)
        try:
            data = json.loads((Path(repo) / rel).read_text("utf-8"))
            entry = get_server(data, target.shape, "ddflow")
        except (OSError, ValueError, AttributeError):
            continue
        cmd, env = _entry_parts(entry)
        if not cmd:
            continue
        missing: list[str] = []
        if os.path.isabs(cmd):
            missing += _gone([cmd])
        elif shutil.which(cmd) is None:
            missing.append(cmd)
        root = env.get("PYTHONPATH")
        if isinstance(root, str) and root:
            dirs = [d for d in root.split(os.pathsep) if d]
            if dirs and not any(
                os.path.isfile(os.path.join(d, "ddflow", "__init__.py")) for d in dirs
            ):
                missing.append(f"{dirs[0]}/ddflow")
        if missing:
            out.append(
                Dangling(f"the ddflow MCP entry in {rel}", tuple(missing), False, "ddflow adopt")
            )
    return out


def findings(repo: Path) -> list[Dangling]:
    """Every dangling launcher in this project: git hooks, harness hooks, MCP entries."""
    from . import enforce as E

    out: list[Dangling] = []
    try:
        hooks = E.hooks_dir(repo)
    except RuntimeError:
        hooks = None
    if hooks is not None:
        for name in E._hooks():
            path = hooks / name
            if path.is_file() and E.HOOK_MARKER in path.read_text("utf-8", errors="replace"):
                if d := check_hook_file(path):
                    out.append(d)
    out += check_settings(repo)
    out += check_mcp(repo)
    return out
