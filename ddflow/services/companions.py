"""Companion tools — detect what is missing, say what it costs, register what can be.

ddflow imposes an order and demands evidence. It does not *perform* the judgement
inside most of its gates: `standards` wants an automated standards review, `research`
wants documentation it can check a claim against, `rules` wants memory of the last time
somebody hit this. A project that installs ddflow and stops has those gates wired to
nothing — and because an agent gate passes on an assertion, that gap is invisible
exactly the way the whole design is meant to prevent.

So the gap is named. `ddflow companions` maps each gate to the servers that serve it,
probes which are actually on this machine, and reports the three states separately:
**installed and registered**, **installed but not registered** (one command away), and
**not installed** (with the command and the URL, never an automatic download).

Two rules shape this file:

* **Nothing is installed automatically.** Registering a server means an agent may
  execute it; fetching and running code on someone's machine because a config file
  named it is not a thing a work-queue tool gets to do. Detection is read-only, the
  report is advice, and `companions add` writes config for servers already present.
* **The registry is data.** `templates/companions.toml` ships the ones ddflow knows
  about; `.ddflow/companions.toml` overrides and extends it. Adding another is a TOML
  block, not a patch to this module. (It said "the four" until there were six — a count
  in prose is a fact with an expiry date.)

* **A companion is not necessarily a server.** `kind = "cli"` is a tool the agent shells
  out to, recommended and detected like any other and never written into an MCP config,
  because a launch entry for something that speaks no JSON-RPC fails at the first gate
  that reaches for it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..infra import paths
from ..infra import proc as P
from .adopt import AGENT_TARGETS

#: How long a detection probe may take. These are `--version`/`--help` calls, but one
#: of them is `npx`, which will happily spend a minute fetching a package the first
#: time. Bounded so `ddflow companions` cannot hang a session start.
DETECT_TIMEOUT_S = 20

#: What a companion IS, which decides what `companions add` can do with it.
#:
#: * ``mcp`` — an MCP server. Registering it writes a launch entry into an agent's MCP
#:   config, and the agent then has its tools.
#: * ``cli`` — a command-line tool the agent SHELLS OUT to. There is no MCP config
#:   entry to write, so `companions add` has nothing to do and says so instead of
#:   pretending.
#:
#: The distinction is not pedantry. `entry()` would happily build a plausible-looking
#: MCP block for any command, an agent would launch it, and it would fail the JSON-RPC
#: handshake at the moment a gate reached for it — a registration that reads as done
#: and is not, which is the vacuous-pass class in config form. OptMem is the live
#: example: real tool, recommended on purpose, and not an MCP server.
KINDS: tuple[str, ...] = ("mcp", "cli")


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
    #: The COMMAND a human runs, and nothing else — it is quoted verbatim in the
    #: report under "ask the operator, then:", so a sentence there renders as an
    #: instruction nobody can copy. Caveats go in `note`.
    install: str = ""
    #: One line of context shown beneath the install command: a gotcha, a wrapper worth
    #: preferring, a licence to check. Separate from `install` because that field is
    #: read as something to paste.
    note: str = ""
    url: str = ""
    default: bool = False
    #: ``mcp`` (default) or ``cli`` — see :data:`KINDS`. Defaulting to ``mcp`` keeps
    #: every existing registry entry and every project override working unchanged.
    kind: str = "mcp"

    @property
    def is_mcp(self) -> bool:
        return self.kind == "mcp"

    def entry(self) -> dict:
        """The MCP config entry an agent launches this server with.

        Refuses for a non-MCP companion rather than returning a block that would be
        written into a config and fail on first launch.
        """
        if not self.is_mcp:
            raise ValueError(
                f"{self.id!r} is a {self.kind} companion, not an MCP server; "
                f"it has no MCP config entry. Install it ({self.install!r}) and "
                f"ddflow will detect it."
            )
        e: dict = {"command": self.command, "args": list(self.args)}
        if self.env:
            e["env"] = dict(self.env)
        return e


@dataclass
class Status:
    companion: Companion
    #: Three-valued: ``None`` means NOBODY LOOKED, which is not the same as absent.
    #: `scan(probe=False)` — used by the MCP handshake, where a detection probe would
    #: make an agent wait on `npx` before it can do anything — produces exactly that.
    #: Collapsing it into `False` would have the instruction block assert "not
    #: installed" on the strength of not having checked, which is the same mistake as
    #: recording an unavailable reviewer as a pass, one layer out.
    installed: bool | None
    registered_in: list[str] = field(default_factory=list)
    detail: str = ""

    @property
    def state(self) -> str:
        if self.registered_in:
            return "registered"
        if self.installed is None:
            return "unknown"
        return "installed" if self.installed else "missing"

    @property
    def usable(self) -> bool | None:
        """Can an agent actually reach this tool right now? THREE answers, not two.

        ONE definition, because "goal state" differs by kind and re-deriving it per
        call site is how four sites came to disagree:

        * an **mcp** companion is usable once an agent is configured to LAUNCH it.
          Installed but unregistered is one command away, and is not usable yet. This
          is always DEFINITE -- it reads a config file, not a probe.
        * a **cli** companion is usable once it is INSTALLED. `registered` is a state
          it cannot reach, so judging it by that reports its gate as unserved while the
          tool sits on the PATH, and tells the operator to run a command that refuses.
          This is `None` when nobody probed.

        The tri-state is not decoration. `scan(probe=False)` -- which the MCP handshake
        uses, so that a session start does not wait on `npx` -- leaves every `installed`
        as `None`. Collapsing that to `False` made "nobody looked" render as "no tool":
        the handshake reported `rules` as having nothing behind it on every connection
        even with the tool on the PATH, and listed it as something to go install. That
        is the three-valued collapse this module's own `is_installed` was rewritten to
        avoid, reintroduced one layer out.
        """
        if self.companion.is_mcp:
            return bool(self.registered_in)
        return self.installed

    @property
    def is_gap(self) -> bool:
        """A DEFAULT companion KNOWN not to be usable.

        `usable is False`, never `not usable` — unknown is not a gap, it is a question.
        Reporting it as a gap is how an unprobed scan turns into a list of things to
        install that may all already be there.
        """
        return self.companion.default and self.usable is False

    @property
    def is_unknown(self) -> bool:
        """A DEFAULT companion nobody probed. Reported separately, because the remedy
        is "check", not "install"."""
        return self.companion.default and self.usable is None

    @property
    def advice(self) -> str:
        """What to DO about this one — the single question every surface actually asks.

        Four answers, and they are not derivable from `state` alone. An mcp companion
        scanned with `probe=False` is DEFINITELY not registered (that reads a config
        file) while its install state is unknown, so it is neither "one command away"
        nor "go install it": the honest advice is "check". Bucketing by `state` put it
        in no bucket at all, and the handshake silently dropped it.

        * ``ok``       — usable; nothing to do.
        * ``register`` — installed, and an agent only needs to be told to launch it.
        * ``install``  — known absent.
        * ``check``    — we did not look, or looked and could not tell. NOT the same as
                         absent, and the remedy is a probe rather than a download.
        """
        if self.usable is True:
            return "ok"
        if self.usable is None:
            return "check"
        if self.companion.is_mcp and self.installed is True:
            return "register"
        if self.installed is False:
            return "install"
        return "check"


def load(repo: Path) -> list[Companion]:
    """The shipped registry, overlaid by the project's own.

    A project entry with an existing id REPLACES it (so a project can point `roborev`
    at its own wrapper); a new id is appended. Reads `.ddflow/config.toml` as well as
    `.ddflow/companions.toml`, through the same loader gates and reviewers use — and
    therefore with the same policy: **an unknown field is an error.** It used to drop
    them silently, so a misspelt `commmand` produced a companion with no command that
    `companions add` would write into an agent's config as a launch line failing
    mid-task.
    """
    from ..infra import tomlcfg

    out: dict[str, Companion] = {}
    shipped = paths.templates_dir() / "companions.toml"
    sources = [shipped, *tomlcfg.config_paths(repo, "companions.toml")]
    for cid, spec in tomlcfg.overlay_array(sources, "companion", Companion, key="id").items():
        # The loader rejects an unknown FIELD; it cannot know that `kind` has a closed
        # vocabulary. An unvalidated `kind = "MCP"` would be neither "mcp" nor "cli",
        # so `is_mcp` is False and the companion silently stops being registrable --
        # a typo that turns into "nothing to register" with no error anywhere.
        kind = spec.get("kind", "mcp")
        if kind not in KINDS:
            raise ValueError(f"companion {cid!r}: kind = {kind!r} is not one of {', '.join(KINDS)}")
        out[cid] = Companion(**spec)
    return list(out.values())


def is_installed(c: Companion) -> tuple[bool | None, str]:
    """Probe, read-only, bounded. Returns (installed, how we know).

    THREE values, because there are three answers:

    * `True`  — the probe ran and succeeded.
    * `False` — a FACT: nothing of that name is on PATH, or the probe ran and said no.
    * `None`  — could not tell. The probe timed out or could not be spawned, and the
      companion's presence is exactly as unknown as before we asked.

    The same distinction the gate runner makes between a check that ran and failed and
    one that could not run, and for the same reason. This docstring has claimed it
    since the function was written while the code returned `False` for a timeout --
    which is how an operator gets sent to install something they already have, on a
    slow machine or a cold `npx` cache. A docstring is not a check; the checks are in
    `tests/test_companions.py`.
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
    except subprocess.TimeoutExpired:
        return None, (
            f"`{' '.join(c.detect)}` did not answer within {DETECT_TIMEOUT_S}s — could "
            f"not tell. Not the same as absent: re-run, or check it by hand."
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"the probe could not be run at all ({exc}) — could not tell"
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


def _cache_path(repo: Path) -> Path:
    # `.ddflow/local/` is already gitignored, which is what a disposable cache wants:
    # it must never be committed, and losing it must cost nothing but a re-probe.
    return repo / ".ddflow" / "local" / "companion-probes.json"


def _read_cache(repo: Path, ttl_s: int) -> dict[str, tuple[bool, str]]:
    """Cached probe results still inside the TTL. Unreadable cache = no cache."""
    if ttl_s <= 0:
        return {}
    try:
        raw = json.loads(_cache_path(repo).read_text("utf-8"))
        cutoff = time.time() - ttl_s
        return {
            cid: (bool(e["installed"]), str(e["detail"]))
            for cid, e in raw.items()
            if isinstance(e, dict) and float(e.get("at", 0)) >= cutoff
        }
    except (OSError, ValueError, KeyError, TypeError):
        # A corrupt cache is a cache miss, never an error. It is derived data whose
        # only job is to be faster than asking again.
        return {}


def _write_cache(repo: Path, fresh: dict[str, tuple[bool, str]]) -> None:
    path = _cache_path(repo)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        merged = {
            cid: {"installed": inst, "detail": detail, "at": now}
            for cid, (inst, detail) in fresh.items()
        }
        path.write_text(json.dumps(merged, indent=2), "utf-8")
    except OSError:
        pass  # a cache that cannot be written is a cache miss next time; nothing breaks


def scan(repo: Path, *, probe: bool = True, ttl_s: int | None = None) -> list[Status]:
    """State of every known companion. ``probe=False`` skips the detection commands.

    Without probing the install state is **unknown**, not absent — see `Status.installed`.

    Results are cached for `[companions] probe_cache_ttl_s`. Each probe shells out, and
    an `npx`-based one takes seconds on a cold cache — fine for `adopt`, which pays it
    once, and not fine for anything a session start might call. `ttl_s=0` re-probes.

    **An inconclusive result is never cached.** A timeout or a failed spawn says only
    that we could not tell just now; storing that would make one blip stick for the
    whole window and report `unknown` about a tool sitting right there.
    """
    if ttl_s is None:
        from ..config import Config

        ttl_s = Config.load(repo).companions.probe_cache_ttl_s
    cached = _read_cache(repo, ttl_s) if probe else {}
    out, fresh = [], dict(cached)
    for c in load(repo):
        if not probe:
            inst, detail = None, "not probed"
        elif c.id in cached:
            inst, detail = cached[c.id]
        else:
            inst, detail = is_installed(c)
            if inst is not None:
                fresh[c.id] = (inst, detail)
        out.append(Status(c, inst, registered_in(repo, c.id), detail))
    if probe and ttl_s > 0 and fresh != cached:
        _write_cache(repo, fresh)
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
    one also decides HOW to launch ddflow itself (uvx vs docker vs source checkout),
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

    "Usable here" means a different thing for the two kinds, and reading it as one
    thing is a bug that hides:

    * an **mcp** companion is usable once an agent is configured to LAUNCH it. Installed
      but unregistered is one command away from usable, and is not usable yet.
    * a **cli** companion is usable once it is INSTALLED. There is nothing to register,
      so judging it by `registered` reports its gate as having nothing behind it while
      the tool sits on the PATH — the report contradicting the line above it.

    Neither counts on `installed is None`. A probe that could not run leaves the tool
    exactly as unknown as before we asked, and counting it would be the
    unavailable-as-success class inside the very report that exists to expose it.
    """
    cover: dict[str, list[str]] = {g: [] for g in pipeline}
    for st in statuses:
        # `is True`, so an unprobed companion does not silently count as coverage --
        # and, equally, does not count as a gap. `gate_coverage` answers "what is
        # behind this gate"; "we did not look" is not an answer either way.
        if st.usable is not True:
            continue
        for g in st.companion.gates:
            if g in cover:
                cover[g].append(st.companion.id)
    return cover
