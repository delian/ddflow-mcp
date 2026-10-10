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

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..infra.fsio import Unreadable, read_json
from . import enforce as E
from .adopt import AGENT_TARGETS, get_server
from .claudehooks import HOOKS, _ours

#: Written by `command_line`: the probe that decides whether the recorded launcher runs.
_PROBE = re.compile(r'\[ -([xf]) "([^"]+)" \]')
#: Lines written before the fallback existed: `exec "<script or python>"` and the
#: package directory on PYTHONPATH.
_LEGACY_EXEC = re.compile(r'exec "([^"]+)"')
_LEGACY_PYPATH = re.compile(r'PYTHONPATH="([^"$]+)')


@dataclass(frozen=True)
class Dangling:
    where: str  #: what holds the record: a hook file, a settings file, an MCP config
    missing: tuple[str, ...]  #: the recorded paths that no longer exist or are not executable
    fallback: bool  #: True when `ddflow` on PATH still runs it
    fix: str  #: the command that refreshes it
    path: str = ""  #: the file that holds the record (absolute, or relative to the project)

    def render(self) -> str:
        gone = ", ".join(self.missing)
        if self.fallback:
            tail = "it falls back to `ddflow` on PATH for now"
        else:
            tail = "NOTHING runs it: the hook fails open and the check is skipped"
        return f"{self.where} records {gone}, which no longer exists or is not executable; {tail} -- run `{self.fix}`"


def _needs(command: str) -> list[tuple[str, bool]]:
    """(path, must be executable) for each file a hook line needs, in recorded order."""
    found = [(p, f == "x") for f, p in _PROBE.findall(command)]
    if not found:
        found = [(p, True) for p in _LEGACY_EXEC.findall(command)]
        found += [(f"{r}/ddflow/__init__.py", False) for r in _LEGACY_PYPATH.findall(command)]
    return list(dict.fromkeys(found))


def _gone(needs: list[tuple[str, bool]]) -> tuple[str, ...]:
    """What is missing -- or no longer executable, which the hook's `[ -x ]` probe also
    treats as gone, so it quietly takes the fallback."""
    return tuple(p for p, x in needs if not (os.access(p, os.X_OK) if x else os.path.exists(p)))


def _on_path() -> bool:
    return shutil.which("ddflow") is not None


def check_command(where: str, command: str, fix: str, path: str = "") -> Dangling | None:
    """A hook command line whose recorded launcher is gone, or None."""
    missing = _gone(_needs(command))
    # Only a line written WITH the fallback has one; an older line execs its dead path.
    return (
        Dangling(where, missing, _on_path() and bool(_PROBE.search(command)), fix, path)
        if missing
        else None
    )


def check_hook_file(path: Path, fix: str = "ddflow hooks install") -> Dangling | None:
    try:
        text = path.read_text("utf-8", errors="replace")
    except OSError:
        return None
    return check_command(f"the git hook {path}", text, fix, str(path))


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

    # Every hook ddflow installs, the pre-compact one included (B4af8a88294): the table
    # is the list, so a hook added to it is checked here without anyone remembering to.
    ours = tuple(dict.fromkeys(h.marker for h in HOOKS))

    out: list[Dangling] = []
    for rel in dict.fromkeys(h.file for h in HOOKS):
        path = Path(repo) / rel
        data = read_json(path)
        if isinstance(data, Unreadable):
            continue
        for cmd in _commands(data):
            if any(_ours({"command": cmd}, m) for m in ours):
                fix = "ddflow hooks install --claude" + (" --gemini" if "gemini" in rel else "")
                if d := check_command(f"the hook command in {rel}", cmd, fix, rel):
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

    out: list[Dangling] = []
    seen: set[str] = set()
    for target in AGENT_TARGETS.values():
        rel = target.config
        if not rel or rel in seen:
            continue
        seen.add(rel)
        data = read_json(Path(repo) / rel)
        if isinstance(data, Unreadable):
            continue
        try:
            entry = get_server(data, target.shape, "ddflow")
        except (ValueError, AttributeError):
            continue
        cmd, env = _entry_parts(entry)
        if not cmd:
            continue
        missing: list[str] = []
        if os.path.isabs(cmd):
            missing += _gone([(cmd, True)])
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
                Dangling(
                    f"the ddflow MCP entry in {rel}", tuple(missing), False, "ddflow adopt", rel
                )
            )
    return out


def findings(repo: Path) -> list[Dangling]:
    """Every dangling launcher in this project: git hooks, harness hooks, MCP entries."""

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
