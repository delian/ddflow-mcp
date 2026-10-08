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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..infra.fsio import Managed, NewerContent, RegionError, Unreadable, read_json
from .enforce import backup_edited

#: What identifies OUR hook among the operator's: the subcommand it runs. Matched as a
#: substring of the command, because the interpreter path in front of it varies.
MARKER = "hooks session-start"
#: The events Claude Code fires SessionStart for. `compact` matters most: it is the
#: moment the model loses the rules it was given at the start.
MATCHER = "startup|resume|clear|compact"
#: The PreCompact hook: `ddflow hooks pre-compact` records what the session was doing
#: before Claude Code compacts it (B195). No matcher: it fires for manual and auto alike.
PRECOMPACT_MARKER = "hooks pre-compact"
PRECOMPACT_EVENT = "PreCompact"
#: The prompt-capture hook: runs `ddflow hooks prompt` with the harness's JSON on stdin.
PROMPT_MARKER = "hooks prompt"
#: Claude Code's event for "the user submitted a prompt", and Gemini CLI's equivalent.
PROMPT_EVENT = "UserPromptSubmit"
GEMINI_PROMPT_EVENT = "BeforeAgent"
GEMINI_SETTINGS = ".gemini/settings.json"
CLAUDE_SETTINGS = ".claude/settings.json"


@dataclass(frozen=True)
class HookSpec:
    """One harness hook ddflow owns: where it lives and what it runs.

    `hooks install/uninstall/status`, `adopt` and `launchers` all read `HOOKS`, so they
    cannot disagree about which hooks are ddflow's, which file holds each, or which
    `--claude`/`--gemini` flag refreshes it.
    """

    agent: str  #: the harness, as `hooks install --<agent>` names it
    name: str  #: the `ddflow hooks <name>` subcommand it runs
    event: str  #: the harness event it fires on
    file: str  #: the repo-relative settings file that holds it
    matcher: str | None  #: the group's matcher; None for an event that takes none
    purpose: str  #: what the install message says it buys
    extra: str = ""  #: arguments appended to the subcommand
    fail_open: bool = True  #: `|| true`: never fail the harness turn it observes
    #: The subcommand the hook runs when that is no longer its `name` (a renamed command,
    #: D-compat): the hook's IDENTITY stays `name`, so a hook installed under the old
    #: command is refreshed in place, never orphaned beside a second one.
    run: str = ""

    @property
    def subcommand(self) -> str:
        """The `ddflow` arguments the hook line runs."""
        return f"hooks {self.run or self.name}"

    @property
    def marker(self) -> str:
        """The hook's IDENTITY, which never changes with the command it runs. A hook entry
        is ours when its stamped region (`hooks/<name>`, the one managed-region grammar,
        D-doc-regions) is there, or, for an entry written before the stamp, when its line
        runs this text."""
        return f"hooks {self.name}"

    @property
    def flag(self) -> str:
        """The `ddflow hooks install` flag that writes (and refreshes) it."""
        return f"--{self.agent}"


#: Every harness hook ddflow installs. Git hooks are not here: they are files, not
#: settings entries, and `enforce._hooks()` is their one table.
HOOKS: tuple[HookSpec, ...] = (
    HookSpec(
        "claude",
        "session-start",
        "SessionStart",
        CLAUDE_SETTINGS,
        MATCHER,
        "every session starts with the ddflow brief",
        fail_open=False,
    ),
    HookSpec(
        "claude",
        "pre-compact",
        PRECOMPACT_EVENT,
        CLAUDE_SETTINGS,
        None,
        # Not "every operator prompt is recorded", which it said until Ba0febd9946.
        "the session's state is recorded before every compaction",
    ),
    HookSpec(
        "claude", "prompt", PROMPT_EVENT, CLAUDE_SETTINGS, None, "every operator prompt is recorded"
    ),
    HookSpec(
        "gemini",
        "prompt",
        GEMINI_PROMPT_EVENT,
        GEMINI_SETTINGS,
        None,
        "every operator prompt is recorded",
        extra="--gemini",
    ),
)


def spec(agent: str, name: str) -> HookSpec:
    """The table row for `agent`'s `name` hook; KeyError when ddflow has none."""
    for h in HOOKS:
        if h.agent == agent and h.name == name:
            return h
    raise KeyError(f"no {agent} {name} hook")


def command(h: HookSpec) -> str:
    """The shell line `h` runs, through `enforce.command_line`'s launcher fallback."""
    from .enforce import command_line

    line = command_line(h.subcommand, extra=h.extra, refresh=f"ddflow hooks install {h.flag}")
    return line + " || true" if h.fail_open else line


def install_spec(repo: Path, h: HookSpec) -> str:
    """Add (or refresh) `h` in its settings file."""
    return install(
        repo,
        command(h),
        event=h.event,
        marker=h.marker,
        matcher=h.matcher,
        rel=h.file,
        purpose=h.purpose,
    )


def uninstall_spec(repo: Path, h: HookSpec) -> str:
    return uninstall(repo, event=h.event, marker=h.marker, rel=h.file)


def state_spec(repo: Path, h: HookSpec) -> tuple[bool | None, str]:
    return state(repo, event=h.event, marker=h.marker, rel=h.file)


def settings_path(repo: Path, rel: str = CLAUDE_SETTINGS) -> Path:
    """The PROJECT settings file: committed, so every clone and worktree gets the hook."""
    return Path(repo) / rel


class SettingsError(ValueError):
    """The settings file exists and is not a JSON object; nothing was written."""


class NewerSettings(SettingsError):
    """A hook entry a NEWER ddflow wrote: not downgraded (D-compat 2), a refusal (exit 3,
    "upgrade ddflow to >= X") rather than a failure."""


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = read_json(path)
    if not isinstance(data, Unreadable):
        return data
    if data.kind == "invalid":
        raise SettingsError(f"{path} is not valid JSON ({data.detail}); not touching it")
    if data.kind == "unreadable":
        # Not a JSONDecodeError, so it escaped every handler and crashed `hooks status`
        # on a file ddflow never wrote (roborev 826).
        raise SettingsError(f"{path} could not be read ({data.detail}); not touching it")
    raise SettingsError(f"{path} is not a JSON object; not touching it")


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
    """Whether a settings entry is ddflow's: it holds the stamped region `_region(marker)`
    -- an identity that survives a rename of the subcommand it runs -- or, written before
    the stamp or by an older ddflow, its command line runs the subcommand `marker` names."""
    if not isinstance(hook, dict):
        return False
    command = str(hook.get("command", ""))
    try:
        if _region(marker).owns(command):
            return True
    except RegionError:
        pass  # broken markers: fall back to the command text
    return marker in command


def _region(marker: str) -> Managed:
    """The stamped region a hook command is written as (fsio.Managed, `hooks/<name>`): a
    shell comment above and below the command line, so the entry says which ddflow wrote
    it and an older one cannot downgrade it. A shell ignores the comments."""
    return Managed(marker.replace(" ", "/"), open="#", close="")


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
    rel: str = CLAUDE_SETTINGS,
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
    rel: str = CLAUDE_SETTINGS,
    purpose: str = "",
) -> str:
    """Add (or refresh) our hook for `event`, leaving every other hook exactly as it was.

    `purpose` is what the message says the hook buys; `install_spec` passes the table's.
    """
    path = settings_path(repo, rel)
    data = _read(path)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise SettingsError(f"{path}: `hooks` is not an object; not touching it")
    groups = hooks.setdefault(event, [])
    if not isinstance(groups, list):
        raise SettingsError(f"{path}: `hooks.{event}` is not a list; not touching it")
    region = _region(marker)
    entry = {"type": "command", "command": region.render(command)}
    for g in groups:
        for i, h in enumerate(_hooks_of(g, path)):
            if _ours(h, marker):
                have = str(h.get("command", ""))
                try:
                    owned = region.owns(have)
                    wanted = region.splice(have, command) if owned else entry["command"]
                    edited = owned and region.state(have) == "edited"
                except NewerContent as exc:
                    raise NewerSettings(f"{path}: the ddflow {event} hook: {exc}") from exc
                except RegionError as exc:
                    raise SettingsError(f"{path}: the ddflow {event} hook: {exc}") from exc
                if wanted == have and h.get("type") == "command":
                    return f"the ddflow {event} hook is already in {path}"
                saved = ""
                if edited:
                    saved = backup_edited(repo, path, "command")
                g["hooks"][i] = {**h, "type": "command", "command": wanted}
                _write(path, data)
                return f"updated the ddflow {event} hook in {path}{saved}"
    groups.append({"matcher": matcher, "hooks": [entry]} if matcher else {"hooks": [entry]})
    _write(path, data)
    if not purpose:
        purpose = next((h.purpose for h in HOOKS if h.event == event and h.marker == marker), "")
    return f"added a {event} hook to {path}" + (f": {purpose}" if purpose else "")


def uninstall(
    repo: Path,
    *,
    event: str = "SessionStart",
    marker: str = MARKER,
    rel: str = CLAUDE_SETTINGS,
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
