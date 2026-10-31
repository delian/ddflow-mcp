"""The first concrete lane: opencode (or its fork kilo) behind the ``Lane`` contract (SW-lane-opencode).

Process route: ``<binary> run --format json`` headless in the item worktree, driven by
``lane_headless.ProcessHeadless`` (a thin subclass states what THIS lane can do). Everything the
lane reads is lane-PRIVATE, under its own home: the provider block (``endpoint``, SW-local-endpoint),
the ddflow MCP server entry with the lane's identity, its instruction file, and the pre-tool plugin
hook (``swarm.lanehooks``, SW-lane-hooks, which asks the operator's consent for the plugin first).
Keys go by env NAME only. Nothing is written to the project, and no model is called here.

Honest by construction (D-swarm-answers): ``select`` prefers opencode and falls back to kilo, and says
which binary and version it used and whether the version was READ; a stream shape or flag nobody read on
the real tool is ``unverified`` in ``FACTS``. ``usage`` and ``steer`` are not advertised (the stream's
token fields were never read, and a headless run takes its prompt as argv), ``resume`` only when asked.
A live run against a real model is the operator's call: ``LIVE_PROBE`` is always ``unavailable``.
ACP (``opencode acp``) can register as a further transport behind the same contract.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from ...config import Config
from ...infra import fsio, paths
from ...infra import proc as P
from .. import mcpconfig, modelreg
from ..engines import stream_map
from ..lane_driver import (
    LaunchSpec,
    Result,
    lane_options,
    register_transport,
    unavailable,
)
from ..lane_headless import ProcessHeadless
from ..swarm import conformance as cf
from ..swarm import lanehooks as LH
from . import endpoint as E

TRANSPORT = "opencode-process"
#: The only argv text: the task itself travels as an attached private file.
MESSAGE = "Do the task described in the attached file."
#: Preference order; the first one on the search path wins (opencode is the reference, kilo its fork).
BINARIES = ("opencode", "kilo")
#: Machine-local override per binary: ``DDFLOW_<NAME>_BIN`` names the executable (never committed).
ENV_PATTERN = "DDFLOW_{}_BIN"
#: Directories searched after PATH (kilo installs under the user's local bin).
EXTRA_DIRS = ("~/.local/bin",)
LIVE_PROBE = unavailable(
    "live smoke run against a model: calls an endpoint and uses a key, so it needs the operator's "
    "consent; run `ddflow lanes smoke` once approved and record the result"
)
#: Facts about the run command and its stream: (value, status, where it was read).
FACTS = {
    "run --format json --model --dir": ("kilo run --help, 7.8.18", "verified"),
    "--auto (permissions)": ("kilo 7.8.18 and opencode 1.19.0 run --help", "verified"),
    "mcp+instructions+provider in home config": (
        "`debug config` on a scratch HOME",
        "verified: kilo 7.8.18, opencode 1.19.0",
    ),
    "plugin dirs (plugin, plugins) in home config": (
        "opencode 1.19.0 debug config",
        "verified; event names unverified",
    ),
    "acp subcommand": ("opencode 1.19.0 acp --help", "exists; not used (later transport)"),
    "json stream shape (step_start, tool_use, error)": (
        "opencode source, from memory",
        "unverified",
    ),
    "usage tokens in stream": ("not read", "unverified: usage not advertised"),
}
#: E5 rules for ``run --format json`` lines. UNVERIFIED shape: re-read against a real run.
STREAM_RULES = (
    {"when": {"path": "type", "equals": "step_start"}, "emit": "started",
     "fields": {"session": "sessionID"}},
    {"when": {"path": "type", "equals": "tool_use"}, "emit": "tool",
     "fields": {"name": "part.tool", "action": "part.state.input.action",
                "error": "part.state.error"}},
    {"when": {"path": "type", "equals": "error"}, "emit": "error",
     "fields": {"message": "error.data.message"}},
)  # fmt: skip


@dataclass(frozen=True)
class Binary:
    name: str  #: harness id too (opencode | kilo)
    path: str
    version: str  #: "" when it could not be read
    read: bool  #: the version was READ from the binary now, not assumed

    def describe(self) -> str:
        v = self.version if self.read else "unknown (unread)"
        return f"{self.name} {v} at {self.path}"


def _version(path: str) -> str:
    got = P.capture([path, "--version"], timeout=30)
    out = got.stdout.strip().splitlines() if got.ran and got.returncode == 0 else []
    return out[-1].strip() if out else ""


def select(
    binaries: tuple[str, ...] = BINARIES,
    *,
    extra_dirs: tuple[str, ...] = EXTRA_DIRS,
    which: Callable[..., str | None] = shutil.which,
    version: Callable[[str], str] = _version,
    environ: Mapping[str, str] | None = None,
) -> Binary | Result:
    """The first of ``binaries`` that is installed, with its version: the ``DDFLOW_<NAME>_BIN``
    variable (an executable path), then PATH, then ``extra_dirs``."""
    environ = os.environ if environ is None else environ
    dirs = os.pathsep.join(str(Path(d).expanduser()) for d in extra_dirs)
    for name in binaries:
        override = environ.get(ENV_PATTERN.format(name.upper()), "")
        found = (
            (which(override) if override else None)
            or which(name)
            or (which(name, path=dirs) if dirs else None)
        )  # '' would mean cwd
        if found:
            found = os.path.abspath(found)  # a relative PATH entry must survive the cwd change
            v = version(found)
            return Binary(name, found, v, bool(v))
    return unavailable(f"none of {list(binaries)} is installed (PATH, {list(extra_dirs)})")


def instructions(agent: str, item: str, worktree: Path) -> str:
    env = Environment(  # nosec B701 - markdown for an agent, not HTML
        loader=FileSystemLoader(str(paths.package_dir() / "templates")),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    return env.get_template("harness/opencode/instructions.md.j2").render(
        agent=agent, item=item, worktree=str(worktree)
    )


def mcp_entry(repo: Path, agent: str, command: list[str] | None = None) -> dict[str, Any]:
    """The lane's ddflow MCP server in the opencode shape, with the lane's own identity."""
    argv = command or [sys.executable, "-m", "ddflow", "--repo", str(repo), "--agent", agent, "mcp"]
    return mcpconfig.server_entry_for(
        mcpconfig.SHAPE_OPENCODE,
        {"command": argv[0], "args": argv[1:], "env": {"DDFLOW_AGENT": agent}},
    )


def _merge_config(path: Path, mcp: dict[str, Any], instr: Path) -> None:
    doc = json.loads(path.read_text("utf-8"))
    doc.setdefault("mcp", {})["ddflow"] = mcp
    doc["instructions"] = [*doc.get("instructions", []), str(instr)]
    fd = os.open(path, os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        os.fchmod(fh.fileno(), 0o600)
        fh.write(json.dumps(doc, indent=2) + "\n")


def argv_for(b: Binary) -> tuple[str, ...]:
    """The headless command; every value a whole-element placeholder (E4). ``--auto`` is in the
    ``run --help`` of both binaries (kilo 7.8.18, opencode 1.19.0)."""
    auto = ("--auto",)
    # the task text is a private file (the closed placeholder list has no prompt); `--file` takes
    # a list, so it comes last and the one-line message first (run --help of both)
    return (b.path, "run", MESSAGE, "--format", "json", "--model", "{model}", "--dir", "{dir}",
            *auto, "--file", "{prompt_file}")  # fmt: skip


@dataclass(frozen=True)
class LanePrep:
    binary: Binary
    spec: LaunchSpec
    hooks: LH.Prepared
    endpoint: E.Prepared
    home: Path

    @property
    def level(self) -> str:
        """Hook strength reported honestly: prevent (plugin) or detect (commit only)."""
        return self.hooks.level


def prepare(  # noqa: PLR0913 - one call that writes the whole private lane
    cat: modelreg.Catalog,
    route: str,
    *,
    repo: Path,
    worktree: Path,
    home: Path,
    agent: str,
    prompt: str,
    item: str = "",
    state: Any = None,
    model: str = "",
    binary: Binary | None = None,
    require_tools: bool = True,
    allow_unverified: bool = False,
    cfg: Config | None = None,
    mcp_command: list[str] | None = None,
) -> LanePrep | LH.Consent | Result:
    """Write the lane's private config and return the launch. ``Consent`` (the plugin needs the
    operator's approval) or an ``unavailable`` Result means no model was reached and the lane
    must not start; some files may remain under ``home``: ``cleanup`` removes them."""
    b = binary or select()
    if isinstance(b, Result):
        return b
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    hooks = LH.prepare(repo, worktree, home, b.name, agent, state=state,
                       allow_unverified=allow_unverified)  # fmt: skip
    if not isinstance(hooks, LH.Prepared):
        return hooks
    ep = E.prepare(cat, route, b.name, home, model, require_tools=require_tools,
                   allow_unverified_harness=allow_unverified)  # fmt: skip
    if isinstance(ep, Result):
        return ep
    instr = home / "ddflow-lane" / "AGENTS.md"
    instr.parent.mkdir(parents=True, exist_ok=True)
    fsio.atomic_write(instr, instructions(agent, item, worktree), mode=0o600)
    _merge_config(ep.config, mcp_entry(repo, agent, mcp_command), instr)
    opts = lane_options(cfg or Config())
    pass_env = tuple(dict.fromkeys((*opts.pop("pass_env"), "PATH")))  # the binary needs PATH
    spec = LaunchSpec(
        name=agent,
        argv=argv_for(b),
        values={"model": ep.model, "dir": str(worktree)},
        prompt=prompt,
        cwd=str(worktree),
        env=ep.env,
        fixed_env=dict(hooks.env),
        home=home,
        pass_env=pass_env,
        stream_rules=stream_map.parse_rules(list(STREAM_RULES)),
        **opts,
    )
    return LanePrep(b, spec, hooks, ep, home)


def cleanup(home: Path) -> str:
    """Remove the hook files and the git hook ``prepare`` installed (the home itself is the caller's)."""
    return LH.remove(Path(home))


class OpenCodeLane(ProcessHeadless):
    """``ProcessHeadless`` that states only what this lane can do: no ``steer`` (the prompt is
    argv), no ``usage`` (stream tokens unread), ``resume`` only if a spec asks."""

    capabilities = frozenset({"interrupt", "tool_events"})


def conformance(
    lp: LanePrep,
    params: cf.Params,
    *,
    revoke: Callable[[], None] | None = None,
    lane: str = "",
) -> cf.Report:
    """Grade the lane (SW-conformance levels 1 to 7) with its own hook probe. Pass a stub
    endpoint and a stub binary in tests; a real model is never reached unless the caller does."""
    return cf.run(
        lane or lp.spec.name,
        OpenCodeLane(),
        lp.spec,
        params,
        hook_probe=LH.hook_probe(lp.home),
        revoke=revoke,
    )


register_transport(TRANSPORT, OpenCodeLane)
