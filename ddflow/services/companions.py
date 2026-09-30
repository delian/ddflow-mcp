"""Companion tools — detect what is missing, say what it costs, register what can be.

ddflow imposes an order and demands evidence. It does not *perform* the judgement
inside most of its gates: `standards` wants an automated standards review, `research`
wants documentation it can check a claim against. (`rules` wants memory of the last
time somebody hit this, and ddflow serves that one itself -- see `BUILTIN_COVERAGE`.) A
project that installs ddflow and stops has the others wired to nothing — and because an agent gate passes on an assertion, that gap is invisible
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
import re
import shlex
import shutil
import subprocess
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ..infra import paths
from ..infra import proc as P
from .adopt import (
    AGENT_TARGETS,
    SHAPE_TOML,
    UnplaceableConfig,
    get_server,
    get_servers,
    place_server,
    server_entry_for,
)

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
    #: Agent -> the server name its config launches this under. Usually the id, but a
    #: project may have registered the same launch under its own name before ddflow
    #: knew it (run_nemo_run's `coding-guides`), and the report names that key.
    registered_as: dict[str, str] = field(default_factory=dict)

    @property
    def state(self) -> str:
        # Only an MCP server is registered. A cli companion named in an agent's MCP
        # config is a leftover entry, and reporting it `registered` showed `[x]` beside
        # a tool that was not on the PATH -- the goal state `usable` already refuses it.
        if self.registered_in and self.companion.is_mcp:
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
    said = [*_said(p.stdout or ""), *_said(p.stderr or "")]
    return True, (said[0][:80] if said else f"`{' '.join(c.detect)}` exited 0")


#: The package manager's own diagnostics: `npx` prints npm's config warnings to stderr
#: ahead of a server that writes nothing to stdout.
_NPM_NOISE = re.compile(r"npm (warn|notice|err)", re.IGNORECASE)

#: Terminal colour codes. `npm_config_color=always` wraps each word of a warning in them,
#: and a filter matching the plain text then lets every coloured warning through.
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _said(output: str) -> list[str]:
    """The lines of a probe's output that say something about the tool.

    The first line was taken as the version whatever it was, so `docker image inspect`
    (a JSON document) read `installed ([)` and an `npx` probe read as an npm warning.
    Structured output is dropped WHOLE, by parsing it rather than by its first
    character: `[codeguide] v1.2` is a line worth showing, and a bracket rule loses it.
    """
    try:
        if isinstance(json.loads(output), (dict, list)):
            return []
    except ValueError:
        pass
    lines = (_ANSI.sub("", line).strip() for line in output.splitlines())
    return [line for line in lines if line and not _NPM_NOISE.match(line)]


#: An npm package spec's version suffix: `pkg@latest`, `@scope/pkg@1.2.3`, `pkg@^2`.
#: Only a TAG-shaped suffix is dropped, so `user@host` keeps its meaning.
_NPM_TAG = re.compile(r"^((?:@[^/@\s]+/)?[^@/\s][^@\s]*)@(?:latest|next|[\^~]?v?\d[\w.+-]*)$")


def _norm_arg(arg: str) -> str:
    m = _NPM_TAG.match(arg)
    return m.group(1) if m else arg


def _launch_of(entry: object) -> tuple[str, list[str]] | None:
    """``(command, args)`` of one stored server entry, whatever the agent's shape.

    opencode/Kilo keep one `command` ARRAY holding the arguments; a few configs write the
    whole launch as one `command` string. A remote server (`url`) has no launch.
    """
    if not isinstance(entry, dict):
        return None
    cmd, args = entry.get("command"), entry.get("args") or []
    if isinstance(cmd, list) and cmd and all(isinstance(x, str) for x in cmd):
        cmd, args = cmd[0], [*cmd[1:], *args]
    if not isinstance(cmd, str) or not cmd.strip() or not isinstance(args, list):
        return None
    if not args and any(ch.isspace() for ch in cmd.strip()):
        try:
            cmd, *args = shlex.split(cmd)
        except ValueError:
            return None
    return cmd, [str(a) for a in args]


def launches_as(c: Companion, entry: object) -> bool:
    """Does this stored server entry launch companion ``c``?

    How strict "the same launch" is, decided once:

    * the same COMMAND, compared by basename (`/usr/local/bin/npx` is `npx`);
    * the companion's ARGUMENTS appear in the entry IN ORDER, other arguments allowed
      between them -- `docker run --rm -i -e TOKEN <image>` is still that image;
    * an npm version tag is ignored on both sides (`@upstash/context7-mcp@latest`);
    * a companion that declares no arguments matches only an entry with none, or every
      server started by the same launcher would count.

    Everything else is a different server: another image or package (a package whose
    name merely starts with the companion's is not it), another launcher, reordered
    arguments. Environment is ignored -- it configures a server, it does not pick one.
    """
    if not c.is_mcp or not c.command:
        return False
    launch = _launch_of(entry)
    if launch is None:
        return False
    cmd, args = launch
    if Path(cmd).name != Path(c.command).name:
        return False
    want = [_norm_arg(a) for a in c.args]
    have = [_norm_arg(a) for a in args]
    if not want:
        return not have
    it = iter(have)
    return all(any(h == w for h in it) for w in want)


def _servers_in(path: Path, shape: str) -> tuple[dict, str] | None:
    """(servers by name, raw text) of one agent config; None when unreadable."""
    try:
        text = path.read_text("utf-8")
    except OSError:
        return None
    if shape == SHAPE_TOML:
        try:
            servers = tomllib.loads(text).get("mcp_servers") or {}
        except tomllib.TOMLDecodeError:
            servers = {}
        return (servers if isinstance(servers, dict) else {}), text
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    # `get_servers` reads the container `get_server` and `place_server` use: ONE
    # declaration of where servers live, so the reader cannot look somewhere the writer
    # does not write. They used to disagree by construction, the reader checking both
    # field names for every agent while the writer special-cased one agent by name.
    return get_servers(data, shape), text


def _registered_name(servers: dict, text: str, shape: str, c: Companion | str) -> str | None:
    """The name one config launches ``c`` under: its id first, else a matching launch."""
    cid = c if isinstance(c, str) else c.id
    if cid in servers or (shape == SHAPE_TOML and f"[mcp_servers.{cid}]" in text):
        return cid
    if isinstance(c, str):
        return None
    for name, entry in servers.items():
        if launches_as(c, entry):
            return name
    return None


def registrations(repo: Path, c: Companion | str) -> dict[str, str]:
    """Agent -> the server name its MCP config registers this companion under.

    Found under the companion's id, or -- given a `Companion` -- under ANY name whose
    launch is the companion's (`launches_as`). A project that registered codeguide-mcp as
    `coding-guides` before ddflow knew it was reported "installed but no agent is
    configured to launch it", and `companions add` offered to write a second copy.
    """
    found: dict[str, str] = {}
    for key, target in AGENT_TARGETS.items():
        if not target.config:
            continue  # no project-level MCP file to look in
        path = Path(repo) / target.config
        if not path.is_file():
            continue
        read = _servers_in(path, target.shape)
        if read is None:
            continue
        name = _registered_name(*read, target.shape, c)
        if name is not None:
            found[key] = name
    return found


def registered_in(repo: Path, c: Companion | str) -> list[str]:
    """Which agents' MCP configs already launch this companion (see `registrations`)."""
    return list(registrations(repo, c))


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
        regs = registrations(repo, c)
        out.append(Status(c, inst, list(regs), detail, regs))
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


def _launched_elsewhere(read: tuple[dict, str] | None, c: Companion) -> str:
    """A message template when a config already launches ``c`` under ANOTHER name.

    Writing the id beside it would start the same server twice, under two names, with
    two copies of every tool -- and an operator whose tools are already named after
    their own key (`mcp__coding-guides__*`) would get a second set they never asked for.
    """
    for name, entry in (read[0] if read else {}).items():
        if name != c.id and launches_as(c, entry):
            return f"{{rel}} already launches {c.id} as `{name}` (the same command); left as it is"
    return ""


def register(repo: Path, c: Companion, agent: str, *, dry_run: bool = False) -> tuple[str, str]:
    """Add one companion to one agent's MCP config, preserving everything there.

    Deliberately the same merge discipline as `adopt._register_mcp`: these files hold
    the operator's other servers and a tool that stomps them is a tool nobody runs
    twice. Kept as its own function rather than generalising that one, because that
    one also decides HOW to launch ddflow itself (uvx vs docker vs source checkout),
    which has no meaning for a third-party server.

    Returns ``(status, message)`` with status one of ``written`` / ``unchanged`` /
    ``refused``. It used to return the message ALONE, so a caller could not tell a write
    from a refusal: an unparseable `.mcp.json` produced
    ``"SKIPPED ...: it is not valid JSON"``, and `cmd_companions` printed that, reported
    ``applied: true`` and exited 0. Nothing was written and the surface said it had
    been — bug class #1 in this repo's own guidelines.

    ``dry_run`` reports exactly what WOULD be written and touches nothing. The
    handshake tells an agent to ask the operator before registering anything, and
    until this existed that instruction had nothing behind it: the agent could only
    describe the change in its own words, or make it and report afterwards. Now it can
    show the operator the actual config entry first, which is the difference between a
    norm and something an operator can act on.
    """
    if agent not in AGENT_TARGETS:
        raise ValueError(f"unknown agent {agent!r}; known: {', '.join(AGENT_TARGETS)}")
    target = AGENT_TARGETS[agent]
    if not target.config:
        return "refused", (
            f"{agent} has no project-level MCP config file; register {c.id} in its own "
            f"settings. `ddflow companions show` prints the entry to paste."
        )
    rel = target.config
    path = Path(repo) / rel

    # ONE decision, made once, for both paths. The dry run used to re-derive the entry
    # by copy-paste, so the two already disagreed: the TOML preview omitted the leading
    # newline the write prepends, and the JSON preview happily reported "WOULD add"
    # over a file the write would REFUSE as unparseable -- an operator signing off on a
    # change that could not happen, which is the failure the preview exists to prevent.
    if target.shape == SHAPE_TOML:
        text = path.read_text("utf-8") if path.exists() else ""
        if f"[mcp_servers.{c.id}]" in text:
            return "unchanged", f"{rel} already registers {c.id}"
        if other := _launched_elsewhere(_servers_in(path, target.shape) if text else None, c):
            return "unchanged", other.format(rel=rel)
        block = (
            f"\n[mcp_servers.{c.id}]\ncommand = {_toml(c.command)}\nargs = {_toml(list(c.args))}\n"
        )
        if c.env:
            block += f"env = {_toml(dict(c.env))}\n"
        new_text = text.rstrip() + "\n" + block if text.strip() else block.lstrip()
        if dry_run:
            # The block as it will be APPENDED, minus the leading blank line that only
            # separates it from what is above. `test_the_preview_matches_the_write_for_a
            # _TOML_target_too` asserts this text appears verbatim in the written file,
            # which is the guarantee that matters; showing the whole merged file here
            # would bury one added stanza in the operator's entire config.
            return "written", f"WOULD add to {rel}:\n{block.lstrip()}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_text, "utf-8")
        return "written", f"registered {c.id} in {rel}"

    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text("utf-8") or "{}")
        except json.JSONDecodeError:
            # Checked BEFORE the dry run reports, so a preview never promises a write
            # that the real call would decline.
            return "refused", f"SKIPPED {rel}: it is not valid JSON; add {c.id} by hand"
    want = server_entry_for(target.shape, c.entry())
    if get_server(data, target.shape, c.id) == want:
        # IDENTICAL, not merely present. The previous version returned early whenever the
        # id existed at all, which lost the refresh the unconditional assignment used to
        # give: an entry whose command had changed in the registry stayed stale forever,
        # and re-running `companions add` -- the obvious remedy -- reported success and
        # did nothing.
        return "unchanged", f"{rel} already registers {c.id} with the same launch command"
    if get_server(data, target.shape, c.id) is None and (
        other := _launched_elsewhere((get_servers(data, target.shape), ""), c)
    ):
        return "unchanged", other.format(rel=rel)
    try:
        place_server(data, target.shape, c.id, c.entry())
    except UnplaceableConfig as exc:
        return "refused", f"SKIPPED {rel}: {exc}; add {c.id} by hand"
    if dry_run:
        # The MERGED result, not a lone entry: the write merges into a file holding the
        # operator's other servers, and a preview showing only the addition misleads in
        # the one way that matters.
        return "written", f"WOULD add to {rel}:\n{json.dumps(data, indent=2)}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", "utf-8")
    return "written", f"registered {c.id} in {rel}"


#: What stands in `gate_coverage` for ddflow's OWN operational memory.
BUILTIN_MEMORY = "ddflow memory"

#: Gates ddflow serves itself, with no companion installed. `rules` is `ddflow brief`:
#: the binding decisions, the lessons ranked against the task, and the operational
#: memory -- facts about this machine, recorded with `ddflow memory add` and searched by
#: `recall`. That last part was the job OptMem and the memory server were recommended
#: for, and once ddflow held it (35dd944) reporting `rules` as having "no companion
#: behind it" sent operators to install a second store beside the first. Named, not
#: silently dropped from the gap list, so the report says WHAT covers the gate.
BUILTIN_COVERAGE: dict[str, tuple[str, ...]] = {"rules": (BUILTIN_MEMORY,)}


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

    One gate has ddflow itself behind it, always: `rules` is `ddflow brief`, which shows
    the operational memory (`ddflow memory`) and the lessons ranked against the task --
    see `BUILTIN_COVERAGE`. Its entry is not a companion id.

    Neither counts on `installed is None`. A probe that could not run leaves the tool
    exactly as unknown as before we asked, and counting it would be the
    unavailable-as-success class inside the very report that exists to expose it.
    """
    cover: dict[str, list[str]] = {g: list(BUILTIN_COVERAGE.get(g, ())) for g in pipeline}
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
