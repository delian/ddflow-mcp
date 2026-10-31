"""The MCP launch entry lines adopt writes into each agent's config."""

from __future__ import annotations

import shutil
from pathlib import Path

from ...infra.paths import launch_parent, launch_python
from .. import install_info as _INSTALL
from ..mcpconfig import SHAPE_NONE
from ..mcpconfig import register as _register_server
from ._refused import Refused
from ._targets import AGENT_TARGETS

#: Where an operator reads the manual step for an agent with no project config.
_DOCS_HINT = "docs/ddflow/drivers/deltas/"

#: The module an agent spawns to get the MCP server, and the directory that must be on
#: `PYTHONPATH` for it to import.
#:
#: Both used to be hardcoded relative to THIS file (`parents[1]`, `ddflow.mcp_server`)
#: and both broke silently when the package was split into layers: the server spawned,
#: could not import, printed nothing, and every client waiting on its handshake hung for
#: as long as it was willing to wait. Derived now — the module name is checked against
#: the import system by `tests/test_packaging.py`, and the parent comes from the
#: package's own `__file__` rather than from counting directory levels above a file that
#: may move again.
MCP_MODULE = "ddflow.surfaces.mcp"


def _package_parent() -> str:
    """The directory containing the `ddflow` package that launch lines point at — what
    goes on PYTHONPATH. The primary checkout when run from a linked worktree of ddflow
    (`infra.paths.launch_parent`)."""

    return str(launch_parent())


def _python() -> str:

    return launch_python()


def _launch_entry(
    launch: str = "auto", image: str = "ghcr.io/OWNER/ddflow:latest"
) -> dict[str, object]:
    """How an MCP client should spawn ddflow.

    ``docker`` is the zero-toolchain option: the operator needs Docker and nothing
    else, and it behaves identically on Linux, macOS and Windows. The flags are not
    decoration —

    * ``-i`` but **never** ``-t``: MCP is newline-delimited JSON-RPC over stdin/stdout,
      and a TTY injects control sequences that corrupt the stream.
    * ``--rm``: one container per session; the state lives in the mounted repo.
    * ``-v <repo>:/repo``: the repo is the only durable thing; everything ddflow
      writes goes there.
    * ``--add-host=host.docker.internal:host-gateway``: lets a reviewer endpoint served
      on the operator's own machine be reachable. Docker Desktop provides the name
      already; on Linux it does not exist without this flag.
    """

    if launch == "auto" and not _running_from_source() and not _installed_from_index():
        # Installed, but not from an index: from git, a local path or an archive URL
        # (bug B8ff258154d). `uvx ddflow-mcp` resolves the name against PyPI, which is
        # a different version at best and a 404 before the first release, so the entry
        # must name THIS installation.
        return _installed_entry()
    if launch == "docker" or (
        launch == "auto"
        and not shutil.which("uvx")
        and not shutil.which("ddflow-mcp")
        and shutil.which("docker")
    ):
        return {
            "command": "docker",
            "args": [
                "run",
                "-i",
                "--rm",
                "-v",
                "${workspaceFolder}:/repo",
                "--add-host=host.docker.internal:host-gateway",
                image,
            ],
        }
    if launch == "python":
        pkg_parent = _package_parent()
        return {
            "command": _python(),
            "args": ["-m", MCP_MODULE],
            "env": {"PYTHONPATH": pkg_parent},
        }
    if shutil.which("uvx") and not _running_from_source():
        return {"command": "uvx", "args": ["ddflow-mcp"]}
    if shutil.which("ddflow-mcp") and not _running_from_source():
        return {"command": "ddflow-mcp", "args": []}
    # Source checkout: point at THIS tree, so a developer's project uses the code they
    # are editing.
    pkg_parent = _package_parent()
    return {
        "command": _python(),
        "args": ["-m", MCP_MODULE],
        "env": {"PYTHONPATH": pkg_parent},
    }


def _running_from_source() -> bool:
    """True when this module lives in a checkout rather than in site-packages."""
    return _INSTALL.running_from_source()


#: The distribution name on the index, and what `uvx` resolves.
DIST_NAME = _INSTALL.DIST_NAME


def _installed_from_index() -> bool:
    """True when this installation came from a package index, so `uvx ddflow-mcp`
    reaches the same project (PEP 610; see `install_info`)."""
    return _INSTALL.installed_from_index()


def _installed_entry() -> dict[str, object]:
    """Launch THIS installation: the `ddflow-mcp` script installed beside the running
    interpreter when there is one (what `uv tool install` / `pipx install` put in the
    tool's environment), else that interpreter with the package's directory on
    PYTHONPATH. Absolute paths both: an agent's environment need not share this PATH."""
    exe = Path(_python())
    for name in (DIST_NAME, DIST_NAME + ".exe"):
        script = exe.parent / name
        if script.is_file():
            return {"command": str(script), "args": []}
    return {
        "command": str(exe),
        "args": ["-m", MCP_MODULE],
        "env": {"PYTHONPATH": _package_parent()},
    }


def _register_mcp(
    repo: Path, key: str, *, launch: str = "auto", image: str = "ghcr.io/OWNER/ddflow:latest"
) -> str:
    """Add the ddflow MCP server to one agent's config, preserving what is there.

    Merged rather than overwritten: these files hold the user's other servers, and a
    tool that stomps them is a tool nobody runs twice.
    """
    target = AGENT_TARGETS[key]
    if not target.config or target.shape == SHAPE_NONE:
        return (
            f"{key}: no project-level MCP config file exists — register the server in its "
            f"own settings (see {_DOCS_HINT})"
        )
    rel = target.config
    # `uvx` fetches and runs the published package in an ephemeral environment, so a
    # project adopting ddflow needs no clone, no virtualenv, no PYTHONPATH and no
    # install step an operator can forget. When ddflow is running from a source
    # checkout rather than an installed distribution, fall back to that checkout --
    # otherwise developing ddflow would silently configure the project against the
    # PUBLISHED version instead of the one under test.
    entry = _launch_entry(launch, image)
    # The writer `companions add` uses too: parsed, a stale entry refreshed, a file it
    # cannot read refused (Ba4cc85bdbc: TOML was a substring test).
    status, msg = _register_server(
        repo / rel,
        target.shape,
        "ddflow",
        entry,
        rel=rel,
        manual="the server",
        ours="ddflow's launch",
    )
    return Refused(msg) if status == "refused" else msg
