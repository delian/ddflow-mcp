"""Onboarding stage 1: the harness wiring that fails silently when it is wrong.

The `onboard` prompt asks an agent to establish three facts by hand. Each is invisible
when it is missing -- nothing errors, the workflow just behaves as if a feature were
absent:

* **A project `.mcp.json` server Claude Code was never told to trust.** Interactive
  sessions show an approval prompt; unattended ones cannot, and a server that never
  starts is the queue itself. The approval is recorded as ``enabledMcpjsonServers`` in
  the COMMITTED ``.claude/settings.json``, so every clone and worktree session inherits
  it.
* **Reviewer endpoints and worker counts that exist only on this machine.** They belong
  under the git-ignored ``.ddflow/local/`` (D-no-own-services-local-dir). A sibling
  project on the same machine has usually already answered "which endpoint" and "how many
  workers"; the OPERATOR chooses whether to reuse it.
* **A shell whose ``ddflow`` is not the code the MCP entry runs.** When the entry pins a
  checkout with ``PYTHONPATH``, ``ddflow <command>`` must run that same checkout, or the
  agent drives a queue with one ddflow while the server answers with another.

Only project files are written, plus -- when asked -- a wrapper in a bin directory the
caller names. User settings are never edited: per Claude Code's own documentation
(code.claude.com/docs/en/mcp), a ``disabledMcpjsonServers`` entry in ANY settings file
rejects a server in every permission mode, so one is REPORTED here, never overridden.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .adopt import SHAPE_MCP_SERVERS, Refused, get_servers
from .claudehooks import SettingsError, _read, _write
from .configwrite import ensure_local_dir

#: Claude Code's COMMITTED project settings. Deliberately not `settings.local.json`:
#: approval has to reach every clone and every worktree session, and an untracked local
#: file is checked out by nobody.
SETTINGS_REL = ".claude/settings.json"
#: The project MCP file whose servers that setting gates.
MCP_REL = ".mcp.json"
ENABLED_KEY = "enabledMcpjsonServers"
DISABLED_KEY = "disabledMcpjsonServers"

#: The machine-local files stage 1 copies from a sibling project, relative to the repo.
#: `gates.toml` carries this machine's worker count; `reviewers.toml` its endpoints.
LOCAL_CONFIGS = (".ddflow/local/reviewers.toml", ".ddflow/local/gates.toml")

#: What marks the wrapper as ddflow's own. A file this marker is not in is the
#: operator's, and is never overwritten.
WRAPPER_MARK = "# ddflow: onboarding wrapper (managed; edits are overwritten)"


class HarnessError(ValueError):
    """A harness file exists and cannot be read as what it must be; nothing was written."""


def project_servers(repo: Path, *, mcp_rel: str = MCP_REL) -> dict[str, Any]:
    """Every MCP server registered in the project, by name.

    Read with `adopt.get_servers`, the same shape vocabulary the writer places them
    with: a second reader that guessed `mcpServers` would drift from the one that wrote
    the file, and a wrong key is valid JSON that is silently ignored.
    """
    path = Path(repo) / mcp_rel
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        raise HarnessError(f"{mcp_rel} could not be read ({exc}); fix it by hand") from exc
    if not isinstance(data, dict):
        raise HarnessError(f"{mcp_rel} is not a JSON object; fix it by hand")
    return get_servers(data, SHAPE_MCP_SERVERS)


def registered_entry(repo: Path, name: str = "ddflow", *, mcp_rel: str = MCP_REL) -> Any:
    """The entry the harness will actually start for `name`, or None."""
    return project_servers(repo, mcp_rel=mcp_rel).get(name)


def enable_project_servers(
    repo: Path, *, mcp_rel: str = MCP_REL, settings_rel: str = SETTINGS_REL
) -> list[str]:
    """Approve every registered server in the committed project settings.

    Returns action lines; a server the operator put in `disabledMcpjsonServers` is a
    `Refused`, because that list "blocks it in every permission mode" and silently
    clearing it would reverse a decision this function has no mandate to touch.

    Raises `SettingsError` (from `claudehooks`) for a settings file that cannot be read
    or holds a non-list where these keys belong: rewriting such a file would drop
    whatever the operator was halfway through writing.
    """
    names = list(project_servers(repo, mcp_rel=mcp_rel))
    if not names:
        return [f"no servers registered in {mcp_rel}; nothing to approve"]
    path = Path(repo) / settings_rel
    data = _read(path)
    enabled = data.get(ENABLED_KEY) or []
    denied = data.get(DISABLED_KEY) or []
    for key, value in ((ENABLED_KEY, enabled), (DISABLED_KEY, denied)):
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise SettingsError(f"{path}: `{key}` is not a list of names; not touching it")
    add = [n for n in names if n not in enabled and n not in denied]
    refused = [
        Refused(
            f"{n} is listed in {DISABLED_KEY} in {settings_rel}, which rejects it in every "
            f"permission mode; remove it there first if the operator wants it started"
        )
        for n in names
        if n in denied
    ]
    if add:
        data[ENABLED_KEY] = [*enabled, *add]
        _write(path, data)
        done = (
            f"approved {', '.join(add)} in {settings_rel} ({ENABLED_KEY}); Claude Code "
            f"still shows its one-time workspace-trust dialog in a folder it has not trusted"
        )
    elif refused:
        done = f"nothing added in {settings_rel}: every registered server is disabled there"
    else:
        done = f"every server in {mcp_rel} is already approved in {settings_rel}"
    return [done, *refused]


def git_ignored(repo: Path, rel: str) -> bool | None:
    """Is `rel` ignored by git here? None when git cannot answer -- not a repo, no git."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "check-ignore", "-q", "--", rel],
            capture_output=True,
        )
    except OSError:
        return None
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    return None


def copy_local_configs(repo: Path, source: Path, *, names: tuple[str, ...] = LOCAL_CONFIGS) -> list[str]:
    """Copy machine-local config from a sibling project, never overwriting what is here.

    `configwrite.ensure_local_dir` creates `.ddflow/local/` with its own `*` `.gitignore`,
    so a project whose `.ddflow/.gitignore` predates `local/` still cannot commit the
    copy. Copies are checked against git before anything is written: a local file that is
    not ignored is a machine endpoint one `git add` away from every clone, and that is a
    refusal, not a warning.

    Existing files are kept -- a local file already tuned HERE is a more recent answer
    about this machine than the sibling's -- and each outcome (copied, kept, absent) is
    reported so the operator can see what changed.
    """
    repo, source = Path(repo), Path(source)
    ensure_local_dir(repo)
    ignored = git_ignored(repo, ".ddflow/local/")
    if ignored is False:
        return [
            Refused(
                f"{repo}/.ddflow/local/ is NOT git-ignored; refusing to copy machine-local "
                f"config into a path that can be committed"
            )
        ]
    out: list[str] = []
    if ignored is None:
        out.append(
            "git could not say whether .ddflow/local/ is ignored (not a repository?); "
            "check before staging anything"
        )
    for rel in names:
        src, dst = source / rel, repo / rel
        if not src.is_file():
            out.append(f"no {rel} in {source}")
            continue
        if dst.exists():
            out.append(f"kept {rel}: already present here")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        out.append(f"copied {rel} from {source}")

    missing = [rel for rel in names if (repo / rel).is_file() and rel not in _local_files(repo)]
    if missing:
        out.append(
            "git-ignored, so a worktree will NOT have these unless listed: "
            + ", ".join(missing)
            + " -- add them to [worktree].local_files so a claim copies them in"
        )
    return out


def _local_files(repo: Path) -> list[str]:
    """`[worktree].local_files` in force here; [] when the config cannot be read.

    A convenience report must not fail the copy: an unreadable config is a different
    problem, surfaced by `doctor`.
    """
    from ..config import Config

    try:
        return list(Config.load(repo).worktree.local_files)
    except Exception:  # a report about worktrees must not break the copy it reports on
        return []


def pinned_cli(entry: Any) -> str | None:
    """The line that runs the CLI from a PYTHONPATH-pinned MCP entry, or None.

    Only a pinned entry has code to mirror: a `uvx`, installed-`ddflow-mcp` or `docker`
    entry names a published artifact or an image, and the shell either finds that same
    installation or cannot reach it at all. The pin is PREPENDED, so it wins over an
    inherited PYTHONPATH entry (R-onboard-harness-pin).
    """
    if not isinstance(entry, dict):
        return None
    pin = (entry.get("env") or {}).get("PYTHONPATH")
    command = entry.get("command")
    if not pin or not command:
        return None
    return (
        f"PYTHONPATH={shlex.quote(str(pin))}${{PYTHONPATH:+:$PYTHONPATH}} "
        f'exec {shlex.quote(str(command))} -m ddflow "$@"'
    )


def wrapper_text(entry: Any) -> str:
    """The full wrapper script for `entry`; ValueError when it is not pinned."""
    line = pinned_cli(entry)
    if line is None:
        raise ValueError("the MCP entry is not PYTHONPATH-pinned; there is no code to mirror")
    return f"#!/bin/sh\n{WRAPPER_MARK}\n{line}\n"


def on_path(directory: Path, *, path: str | None = None) -> bool:
    """Is `directory` a PATH entry? Resolved by absolute path, so `bin` and `./bin` match."""
    wanted = os.path.abspath(str(directory))
    raw = path if path is not None else os.environ.get("PATH", "")
    return any(part and os.path.abspath(part) == wanted for part in raw.split(os.pathsep))


def install_shell_command(
    entry: Any, *, bindir: str | Path | None = None, path: str | None = None
) -> str:
    """Write the `ddflow` wrapper a harness's shell needs, and say where it landed.

    The operator's own `ddflow` is never overwritten: a file without this module's marker
    is theirs, and is refused. A wrapper already written is refreshed in place when the
    pin changes, so re-running onboarding is safe and one run is enough.
    """
    line = pinned_cli(entry)
    if line is None:
        return (
            "the MCP entry is not PYTHONPATH-pinned (uvx, an installed ddflow-mcp or "
            "docker): there is no checkout for the shell to mirror"
        )
    directory = Path(bindir) if bindir else Path.home() / ".local" / "bin"
    target = directory / "ddflow"
    text = wrapper_text(entry)
    if target.exists() or target.is_symlink():
        try:
            existing = target.read_text("utf-8", errors="replace")
        except OSError as exc:
            return Refused(f"{target} could not be read ({exc}); not touching it")
        if WRAPPER_MARK not in existing:
            return Refused(
                f"{target} is not ddflow's wrapper; not touching it -- move it aside and "
                f"re-run if the pinned checkout should replace it"
            )
        if existing == text:
            return f"the shell command is already at {target}"
        target.write_text(text, "utf-8")
        target.chmod(0o755)
        return f"updated {target} to the pinned checkout"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        target.write_text(text, "utf-8")
        target.chmod(0o755)
    except OSError as exc:
        return Refused(f"could not write {target} ({exc}); create the wrapper by hand")
    note = (
        ""
        if on_path(directory, path=path)
        else f'; add it to PATH first: export PATH="{directory}:$PATH"'
    )
    return f"wrote {target}: the shell runs the pinned checkout{note}"
