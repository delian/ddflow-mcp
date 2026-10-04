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
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ..infra.worktree import copy_local_files, tracks_local_file
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
    # `.get(KEY, [])`, NOT `.get(KEY) or []`: a falsy non-list (`{}`, `""`, `false`)
    # would be silently replaced by a list, rewriting exactly the malformed file the
    # type check below exists to leave alone (rubber_duck on 91c639e).
    enabled = data.get(ENABLED_KEY, [])
    denied = data.get(DISABLED_KEY, [])
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


def copy_local_configs(
    repo: Path, source: Path, *, names: tuple[str, ...] = LOCAL_CONFIGS
) -> list[str]:
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
    local_dir = Path(repo) / ".ddflow" / "local"
    if (
        local_dir.is_dir()
        and (local_dir / ".gitignore").exists()  # ensure_local_dir would write nothing
        and git_ignored(repo, ".ddflow/local/") is False
    ):
        # Refuse BEFORE anything can be written: the `Refused` contract is "nothing was
        # written", and a project that actively un-ignores `local/` has to be fixed
        # first, not worked around (critic on 6de8b73). A directory WITHOUT its own
        # ignore file is not this case -- `ensure_local_dir` creating it IS the fix.
        return [
            Refused(
                f"{repo}/.ddflow/local/ is NOT git-ignored; refusing to copy machine-local "
                f"config into a path that can be committed. Nothing was written."
            )
        ]
    ensure_local_dir(repo)
    ignored = git_ignored(repo, ".ddflow/local/")
    if ignored is False:
        return [
            Refused(
                f"{repo}/.ddflow/local/ is still NOT git-ignored after creating its own "
                f".gitignore (a tracked file there?); nothing was copied"
            )
        ]
    out: list[str] = []
    if ignored is None:
        out.append(
            "git could not say whether .ddflow/local/ is ignored (not a repository?); "
            "check before staging anything"
        )
    # The copy itself is `infra.worktree.copy_local_files` -- the same operation a claim
    # runs (git-ignored machine-local files from one checkout into another, never
    # overwriting, never writing through a symlink): one implementation, not two that
    # drift (dedupe on the branch review).
    copied = copy_local_files(source, repo, list(names))
    source_root = Path(source).resolve()
    for rel in names:
        # `rel in copied` FIRST: after the helper ran, `dst.exists()` is true for exactly
        # the files it just copied, so a later check would report every copy as "kept".
        if not (source / rel).is_file():
            out.append(f"no {rel} in {source}")
        elif rel in copied:
            out.append(f"copied {rel} from {source}")
        elif (repo / rel).exists() or (repo / rel).is_symlink():
            out.append(f"kept {rel}: already present here")
        elif not (source / rel).resolve().is_relative_to(source_root):
            out.append(f"skipped {rel}: the source path resolves outside {source}")
        elif tracks_local_file(source, rel):
            out.append(f"skipped {rel}: tracked in {source}; committed policy, not machine-local")
        else:
            # Every other reason the helper skips is NAMED above, so this is a copy that
            # failed (permissions, disk). Saying "likely tracked" here cost the operator
            # the machine's endpoints with no error (delta hunt on 8f88cc0).
            out.append(
                Refused(
                    f"could not copy {rel}: the copy failed (permissions or disk?); "
                    f"nothing was written -- copy it by hand and re-run"
                )
            )

    present = [rel for rel in names if (repo / rel).is_file()]
    listed = _local_files(repo)
    if listed is None:
        # "could not tell" is NOT "not listed": telling the operator to add entries that
        # may already be there is the three-valued collapse roborev flagged on e9bbfd74.
        if present:
            out.append(
                "could not read [worktree].local_files; check whether these reach "
                "worktrees: " + ", ".join(present)
            )
    else:
        missing = [rel for rel in present if rel not in listed]
        if missing:
            out.append(
                "git-ignored, so a worktree will NOT have these unless listed: "
                + ", ".join(missing)
                + " -- add them to [worktree].local_files so a claim copies them in"
            )
    return out


def _local_files(repo: Path) -> list[str] | None:
    """`[worktree].local_files` in force here; None when the config cannot be read.

    A convenience report must not fail the copy, but it must not guess either: None
    keeps "could not read the config" distinct from "read it, and they are not listed".
    """
    from ..config import Config

    try:
        return list(Config.load(repo).worktree.local_files)
    except Exception:  # a report about worktrees must not break the copy it reports on
        return None


def _resolve_path(part: str, base: Path | None) -> str:
    """`part` absolute, resolving a relative one against `base` (the project root).

    The MCP client starts a project-scoped entry with the project as its working
    directory; the wrapper runs from wherever the shell is, so a relative path that is
    copied verbatim points somewhere else -- a different checkout, or nowhere
    (rubber_duck on 91c639e). `~` expands against the HOME of whoever writes the
    wrapper, which is the machine the harness runs on (roborev on e9bbfd74).
    """
    path = Path(os.path.expanduser(part))
    if path.is_absolute():
        return str(path)
    if base is None:
        raise ValueError(
            f"the path {part!r} is relative and no project root was given to resolve it "
            f"against; the wrapper would run from whatever directory the shell is in"
        )
    return str((Path(base) / path).resolve())


def _resolve_command(command: str, base: Path | None) -> str:
    """The entry's command, absolute when it is a path, unchanged when it is a bare name.

    `python`, `uv` and `ddflow-mcp` are PATH lookups for the client too; resolving them
    against the project would invent a file that does not exist there.
    """
    text = os.path.expanduser(command.strip())
    bare = (
        not text.startswith((".", "~"))
        and os.sep not in text
        and not (os.altsep and os.altsep in text)
    )
    return text if bare else _resolve_path(text, base)


def _cli_argv(entry: dict) -> list[str]:
    """The entry's argv prefix with its `-m <module>` pair replaced by `-m ddflow`.

    Preserving the prefix is what keeps an entry like `uv run python -m ddflow.mcp`
    mirroring as `uv run python -m ddflow` rather than the broken `uv -m ddflow`
    (rubber_duck on 91c639e). An entry with no `-m <module>` at all has no CLI
    invocation to derive from it, and is refused rather than guessed at.
    """
    args = entry.get("args") or []
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ValueError("the MCP entry's `args` is not a list of strings")
    for i, arg in enumerate(args):
        if arg == "-m" and i + 1 < len(args):
            # Anything after the module belonged to the server invocation; the CLI takes
            # its own arguments, and carrying them over would silently parse as flags.
            return [*args[:i], "-m", "ddflow"]
    raise ValueError(
        "the MCP entry has no `-m <module>` in its args, so there is no CLI invocation to "
        "mirror; install ddflow so the shell finds the same version, or write the "
        "wrapper by hand"
    )


def pinned_cli(entry: Any, *, base: Path | None = None) -> str | None:
    """The line that runs the CLI from a PYTHONPATH-pinned MCP entry, or None.

    Only a pinned entry has code to mirror: a `uvx`, installed-`ddflow-mcp` or `docker`
    entry names a published artifact or an image, and the shell either finds that same
    installation or cannot reach it at all. The pin is PREPENDED, so it wins over an
    inherited PYTHONPATH entry (R-onboard-harness-pin). Raises ValueError for a pinned
    entry that cannot be mirrored (no `-m <module>`, or a relative path without `base`).
    """
    if not isinstance(entry, dict):
        return None
    env = entry.get("env")
    if env is not None and not isinstance(env, dict):
        # An `env` that is a list or a string used to raise AttributeError out of the
        # caller instead of the promised ValueError (roborev on e9bbfd74).
        raise ValueError("the MCP entry's `env` is not a JSON object")
    pin = (env or {}).get("PYTHONPATH")
    command = entry.get("command")
    if not pin or not command:
        return None
    argv = _cli_argv(entry)
    parts = str(pin).split(os.pathsep)
    pin_text = os.pathsep.join(_resolve_path(p, base) for p in parts if p)
    invocation = " ".join(
        shlex.quote(part) for part in (_resolve_command(str(command), base), *argv)
    )
    return f'PYTHONPATH={shlex.quote(pin_text)}${{PYTHONPATH:+:$PYTHONPATH}} exec {invocation} "$@"'


def wrapper_text(entry: Any, *, base: Path | None = None) -> str:
    """The full wrapper script for `entry`; ValueError when it cannot be mirrored."""
    line = pinned_cli(entry, base=base)
    if line is None:
        raise ValueError("the MCP entry is not PYTHONPATH-pinned; there is no code to mirror")
    return f"#!/bin/sh\n{WRAPPER_MARK}\n{line}\n"


def on_path(directory: Path, *, path: str | None = None) -> bool:
    """Is `directory` a PATH entry? Resolved by absolute path, so `bin` and `./bin` match."""
    wanted = os.path.abspath(str(directory))
    raw = path if path is not None else os.environ.get("PATH", "")
    return any(part and os.path.abspath(part) == wanted for part in raw.split(os.pathsep))


def install_shell_command(
    entry: Any,
    *,
    repo: str | Path | None = None,
    bindir: str | Path | None = None,
    path: str | None = None,
) -> str:
    """Write the `ddflow` wrapper a harness's shell needs, and say where it landed.

    `repo` is the project root the entry is registered in: needed to make a relative
    command or PYTHONPATH absolute. `bindir` is where the caller wants the wrapper, and
    it is NOT defaulted: without it this only PROPOSES `~/.local/bin` and writes nothing,
    because a library that installs a machine-wide command on a default is exactly what
    the operator should be asked about (critic on 6de8b73). The operator's own `ddflow`
    is never overwritten: a file without this module's marker is theirs, and is refused.
    A wrapper already written is refreshed in place when the pin changes, so re-running
    onboarding is safe and one run is enough.
    """
    base = Path(repo) if repo is not None else None
    try:
        line = pinned_cli(entry, base=base)
    except ValueError as exc:
        return Refused(str(exc))
    if line is None:
        return (
            "the MCP entry is not PYTHONPATH-pinned (uvx, an installed ddflow-mcp or "
            "docker): there is no checkout for the shell to mirror"
        )
    directory = Path(bindir) if bindir else Path.home() / ".local" / "bin"
    if bindir is None:
        return (
            f"not installed: name the bin directory to write it to (proposed: {directory}); "
            f"the wrapper line is: {line}"
        )
    target = directory / "ddflow"
    text = wrapper_text(entry, base=base)
    if target.is_symlink():
        # Reading through the link found the marker, but writing would follow it and
        # rewrite whatever it points at, not the link (roborev on e9bbfd74).
        return Refused(
            f"{target} is a symlink; not writing through it -- point PATH at the real "
            f"wrapper, or remove the link and re-run"
        )
    if target.exists():
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
        try:
            _write_executable(target, text)
        except OSError as exc:
            return Refused(f"could not update {target} ({exc}); replace it by hand")
        return f"updated {target} to the pinned checkout"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        _write_executable(target, text)
    except OSError as exc:
        return Refused(f"could not write {target} ({exc}); create the wrapper by hand")
    note = (
        ""
        if on_path(directory, path=path)
        else f'; add it to PATH first: export PATH="{directory}:$PATH"'
    )
    return f"wrote {target}: the shell runs the pinned checkout{note}"


def _write_executable(path: Path, text: str) -> None:
    """Atomically replace `path` with an executable script.

    A temp file in the same directory plus ONE `os.replace`: a plain write truncates
    first, and a failure midway leaves a wrapper that runs nothing. The replace also
    never follows a symlink -- rename(2) replaces the link itself -- although the caller
    refuses symlinks outright rather than silently severing the operator's link
    (roborev on e9bbfd74).
    """
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        tmp.chmod(0o755)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
