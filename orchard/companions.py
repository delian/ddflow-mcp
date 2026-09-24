"""Companion MCP servers — detect what is missing, say what it costs, register it.

Orchard imposes an order and demands evidence. It does not *perform* the judgement
inside most of its gates: `standards` wants an automated standards review, `research`
wants documentation it can check a claim against, `rules` wants memory of the last time
somebody hit this. A project that installs Orchard and stops has those gates wired to
nothing — and because an agent gate passes on an assertion, that gap is invisible
exactly the way the whole design is meant to prevent.

So the gap is named. `orchard companions` maps each gate to the servers that serve it,
probes which are actually on this machine, and reports the three states separately:
**installed and registered**, **installed but not registered** (one command away), and
**not installed** (with the command and the URL, never an automatic download).

Two rules shape this file:

* **Nothing is installed automatically.** Registering a server means an agent may
  execute it; fetching and running code on someone's machine because a config file
  named it is not a thing a work-queue tool gets to do. Detection is read-only, the
  report is advice, and `companions add` writes config for servers already present.
* **The registry is data.** `templates/companions.toml` ships the four Orchard knows
  about; `.orchard/companions.toml` overrides and extends it. Adding a fifth is a TOML
  block, not a patch to this module.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import proc as P
from .adopt import AGENT_TARGETS

#: How long a detection probe may take. These are `--version`/`--help` calls, but one
#: of them is `npx`, which will happily spend a minute fetching a package the first
#: time. Bounded so `orchard companions` cannot hang a session start.
DETECT_TIMEOUT_S = 20


@dataclass
class Companion:
    id: str
    title: str = ""
    gates: list[str] = field(default_factory=list)
    why: str = ""
    detect: list[str] = field(default_factory=list)
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    install: str = ""
    url: str = ""
    default: bool = False

    def entry(self) -> dict:
        """The MCP config entry an agent launches this server with."""
        e: dict = {"command": self.command, "args": list(self.args)}
        if self.env:
            e["env"] = dict(self.env)
        return e


@dataclass
class Status:
    companion: Companion
    installed: bool
    registered_in: list[str] = field(default_factory=list)
    detail: str = ""

    @property
    def state(self) -> str:
        if self.registered_in:
            return "registered"
        return "installed" if self.installed else "missing"


def load(repo: Path) -> list[Companion]:
    """The shipped registry, overlaid by the project's own.

    A project entry with an existing id REPLACES it (so a project can point `roborev`
    at its own wrapper); a new id is appended. Reads `.orchard/config.toml` as well as
    `.orchard/companions.toml`, through the same loader gates and reviewers use — and
    therefore with the same policy: **an unknown field is an error.** It used to drop
    them silently, so a misspelt `commmand` produced a companion with no command that
    `companions add` would write into an agent's config as a launch line failing
    mid-task.
    """
    from . import tomlcfg

    out: dict[str, Companion] = {}
    shipped = Path(__file__).resolve().parent / "templates" / "companions.toml"
    paths = [shipped, *tomlcfg.config_paths(repo, "companions.toml")]
    for cid, spec in tomlcfg.overlay_array(paths, "companion", Companion, key="id").items():
        out[cid] = Companion(**spec)
    return list(out.values())


def is_installed(c: Companion) -> tuple[bool, str]:
    """Probe, read-only, bounded. Returns (installed, how we know).

    A missing executable and a probe that ran and failed are different facts and are
    reported as such — the same distinction the gate runner makes between a failed
    check and one that could not run, for the same reason: "could not tell" must never
    render as "no".
    """
    if not c.detect:
        return False, "no detection probe declared"
    exe = shutil.which(c.detect[0])
    if not exe:
        return False, f"{c.detect[0]} is not on PATH"
    try:
        p = P.run(
            c.detect,
            capture_output=True,
            text=True,
            timeout=DETECT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"probe failed to run: {exc}"
    if p.returncode != 0:
        return False, f"`{' '.join(c.detect)}` exited {p.returncode}"
    first = (p.stdout or p.stderr).strip().splitlines()
    return True, (first[0][:80] if first else exe)


#: Agents whose MCP config keys servers under `servers` rather than `mcpServers`.
#: Copilot / VS Code is the one; everyone else uses `mcpServers`.
#:
#: One list, consulted by BOTH the reader and the writer. They used to disagree: the
#: reader checked both field names for every agent while the writer chose by comparing
#: the agent name to the literal "copilot". Adding a seventh agent that uses `servers`
#: would have had the reader find its entry while the writer created a second, dead
#: `mcpServers` block beside it — and the symptom would be a companion that reports as
#: registered and never launches.
SERVERS_FIELD_AGENTS: frozenset[str] = frozenset({"copilot"})


def _json_field(agent: str) -> str:
    return "servers" if agent in SERVERS_FIELD_AGENTS else "mcpServers"


def registered_in(repo: Path, cid: str) -> list[str]:
    """Which agents' MCP configs already name this server."""
    found = []
    for key, (_delta, rel) in AGENT_TARGETS.items():
        path = Path(repo) / rel
        if not path.is_file():
            continue
        text = path.read_text("utf-8")
        if rel.endswith(".toml"):
            if f"[mcp_servers.{cid}]" in text:
                found.append(key)
            continue
        try:
            data = json.loads(text or "{}")
        except json.JSONDecodeError:
            continue
        if cid in (data.get(_json_field(key)) or {}):
            found.append(key)
    return found


def scan(repo: Path, *, probe: bool = True) -> list[Status]:
    out = []
    for c in load(repo):
        inst, detail = is_installed(c) if probe else (False, "not probed")
        out.append(Status(c, inst, registered_in(repo, c.id), detail))
    return out


def _toml(value: object) -> str:
    """Serialise one value as TOML. There is no stdlib writer; `tomllib` only reads.

    This was `json.dumps`, which is *nearly* right and therefore worse than obviously
    wrong: JSON and TOML agree on strings, numbers and arrays, and disagree on exactly
    one thing — an object. `json.dumps({"A": "b"})` is `{"A": "b"}`, and a TOML inline
    table needs `{A = "b"}`. So a companion carrying `env` (the normal shape for
    anything needing an API key) wrote a config the agent's TOML parser rejects
    outright, which takes down *every* MCP server in that file, not just this one —
    while `registered_in` still reported the companion as registered, because it looks
    for the section header with a substring test.

    String escaping matters for the same reason: a `"` or a backslash in a command or
    an argument produced invalid TOML, silently, for anyone whose path has one.
    """
    if isinstance(value, str):
        return json.dumps(value)  # TOML basic strings and JSON strings escape alike
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k} = {_toml(v)}" for k, v in value.items()) + "}"
    return json.dumps(str(value))


def register(repo: Path, c: Companion, agent: str) -> str:
    """Add one companion to one agent's MCP config, preserving everything there.

    Deliberately the same merge discipline as `adopt._register_mcp`: these files hold
    the operator's other servers and a tool that stomps them is a tool nobody runs
    twice. Kept as its own function rather than generalising that one, because that
    one also decides HOW to launch Orchard itself (uvx vs docker vs source checkout),
    which has no meaning for a third-party server.
    """
    if agent not in AGENT_TARGETS:
        raise ValueError(f"unknown agent {agent!r}; known: {', '.join(AGENT_TARGETS)}")
    _delta, rel = AGENT_TARGETS[agent]
    path = Path(repo) / rel
    path.parent.mkdir(parents=True, exist_ok=True)

    if rel.endswith(".toml"):
        text = path.read_text("utf-8") if path.exists() else ""
        if f"[mcp_servers.{c.id}]" in text:
            return f"{rel} already registers {c.id}"
        block = (
            f"\n[mcp_servers.{c.id}]\ncommand = {_toml(c.command)}\nargs = {_toml(list(c.args))}\n"
        )
        if c.env:
            block += f"env = {_toml(dict(c.env))}\n"
        path.write_text(text.rstrip() + "\n" + block if text.strip() else block.lstrip(), "utf-8")
        return f"registered {c.id} in {rel}"

    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text("utf-8") or "{}")
        except json.JSONDecodeError:
            return f"SKIPPED {rel}: it is not valid JSON; add {c.id} by hand"
    data.setdefault(_json_field(agent), {})[c.id] = c.entry()
    path.write_text(json.dumps(data, indent=2) + "\n", "utf-8")
    return f"registered {c.id} in {rel}"


def gate_coverage(repo: Path, statuses: list[Status], pipeline: list[str]) -> dict[str, list[str]]:
    """Gate id -> the companions serving it that are actually usable here.

    The empty lists are the interesting ones: a gate in your pipeline with no server
    behind it is a gate whose outcome is one model's unaided assertion.
    """
    cover: dict[str, list[str]] = {g: [] for g in pipeline}
    for st in statuses:
        if st.state != "registered":
            continue
        for g in st.companion.gates:
            if g in cover:
                cover[g].append(st.companion.id)
    return cover
