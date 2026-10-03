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

import contextlib
import json
import os
import re
import select
import shlex
import shutil
import signal
import subprocess
import tempfile
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


#: How long `verify` waits for an `initialize` answer. Longer than a detection probe: the
#: launch command is the real server, and an `npx` one downloads itself on a cold cache.
VERIFY_TIMEOUT_S = 30

#: The protocol revision `verify` offers. A server that wants another answers with its own,
#: and that still proves it speaks MCP, so the exact value is not what is being tested.
_VERIFY_PROTOCOL = "2025-03-26"

#: Most of a server's output kept while waiting for its answer.
_MAX_BUF = 1 << 20

#: What an echoing binary (`cat`) sends back: our own request, recognisable by its method.
_ECHO_MARK = b'"method": "initialize"'


@dataclass
class Verification:
    """The result of launching one companion and speaking MCP to it.

    ``speaks_mcp`` is three-valued for the reason `is_installed` is: ``True`` (it answered
    `initialize`), ``False`` (a FACT: the command is absent, exited, or answered something
    that is not a JSON-RPC response -- `cat` echoes the request back, which is not an
    answer), ``None`` (could not tell: no answer in time, e.g. a cold `npx` cache).
    """

    companion: Companion
    speaks_mcp: bool | None
    detail: str
    server: dict = field(default_factory=dict)
    elapsed_s: float = 0.0


def _reply_to(buf: bytes, want_id: int) -> tuple[dict | None, bytes]:
    """The first complete JSON-RPC *response* to `want_id` in `buf`, and the rest.

    A response has `result` or `error` and no `method`: a request echoed back (`cat`) or a
    server's own notification is not one. Lines that are not JSON are skipped -- a server
    may log to stdout before it speaks.
    """
    while b"\n" in buf:
        line, buf = buf.split(b"\n", 1)
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if (
            isinstance(msg, dict)
            and msg.get("jsonrpc") == "2.0"
            and msg.get("id") == want_id
            and "method" not in msg
            and ("result" in msg or "error" in msg)
        ):
            return msg, buf
    return None, buf


def _describe_answer(msg: dict) -> tuple[str, dict]:
    """(detail, server facts) for a JSON-RPC response to `initialize`."""
    result = msg.get("result")
    if not isinstance(result, dict):
        return "answered initialize with a JSON-RPC error (it speaks the protocol)", {}
    info = result.get("serverInfo")
    server = dict(info) if isinstance(info, dict) else {}
    if result.get("protocolVersion"):
        server["protocolVersion"] = result["protocolVersion"]
    who = " ".join(str(server[k]) for k in ("name", "version") if server.get(k))
    return f"answered initialize ({who or 'no serverInfo'})", server


def _exited(proc: subprocess.Popen) -> bool:
    """Has the direct child exited? WITHOUT reaping it (`poll`/`wait` would).

    An unreaped leader keeps its pid -- and so the process group's id -- reserved, which is
    what makes the final group-wide SIGKILL safe: once reaped, the pid may be recycled
    and `killpg` would hit whatever got it.
    """
    try:
        return os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
    except ChildProcessError:
        return True


def _stop(proc: subprocess.Popen) -> None:
    """End the launched server and EVERYTHING it started (an `npx` wrapper has children).

    SIGTERM to the process group, a short grace for the direct child, then SIGKILL to the
    group unconditionally -- the direct child exiting says nothing about a grandchild that
    ignores SIGTERM -- and only then reap the leader.
    """
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGTERM)
    deadline = time.monotonic() + 3
    while not _exited(proc) and time.monotonic() < deadline:
        time.sleep(0.02)
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5)


def _keep(buf: bytes, chunk: bytes) -> bytes:
    """`buf` plus `chunk`, truncated to the last `_MAX_BUF` bytes (a newline-free flood must
    not grow the buffer, or make each read copy all of it)."""
    return (buf + chunk)[-_MAX_BUF:]


def verify_one(c: Companion, *, timeout_s: float = VERIFY_TIMEOUT_S) -> Verification:
    """Launch `command args` and require a JSON-RPC answer to `initialize`.

    The only check that tells a server from a binary with a plausible name (B113 was a
    companion whose launch command was not an MCP server at all). It SPAWNS a process, so
    it is opt-in and is never called by `scan`: the MCP handshake calls `scan`.
    """
    t0 = time.monotonic()

    def done(ok: bool | None, detail: str, server: dict | None = None) -> Verification:
        return Verification(c, ok, detail, server or {}, round(time.monotonic() - t0, 2))

    if not c.is_mcp:
        return done(None, f"{c.id} is a {c.kind} companion: there is no MCP server to launch")
    if not c.command:
        return done(False, "no launch command declared")
    exe = shutil.which(c.command)
    if not exe:
        return done(False, f"`{c.command}` is not on PATH")
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": _VERIFY_PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "ddflow-verify", "version": "0"},
        },
    }
    with tempfile.TemporaryFile() as err:
        try:
            proc = P.popen(
                [exe, *c.args],
                stdin=P.PIPE,
                stdout=P.PIPE,
                stderr=err,
                env={**os.environ, **c.env},
                start_new_session=True,
            )
        except OSError as exc:
            return done(False, f"could not launch `{c.command}`: {exc}")

        def stderr_tail() -> str:
            err.seek(0, os.SEEK_END)
            err.seek(max(0, err.tell() - 8192))  # the tail only: a chatty server's log is big
            lines = [x.strip() for x in err.read().decode("utf-8", "replace").splitlines()]
            lines = [x for x in (_ANSI.sub("", x) for x in lines) if x]
            return f" stderr: {lines[-1][:160]}" if lines else ""

        try:
            try:
                assert proc.stdin is not None and proc.stdout is not None
                proc.stdin.write((json.dumps(request) + "\n").encode())
                proc.stdin.flush()
            except OSError:
                return done(False, f"`{c.command}` exited before reading a request.{stderr_tail()}")
            fd = proc.stdout.fileno()
            buf, deadline = b"", t0 + timeout_s
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    return done(
                        None,
                        f"no answer to `initialize` within {timeout_s:g}s -- could not tell "
                        f"(a cold `npx` cache is slow; a binary that is not a server never answers)."
                        + stderr_tail(),
                    )
                ready, _, _ = select.select([fd], [], [], min(left, 0.5))
                if not ready:
                    if proc.poll() is not None:
                        break
                    continue
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                buf = _keep(buf, chunk)
                if _ECHO_MARK in buf:
                    return done(False, f"`{c.command}` echoed the request back: it is not a server")
                msg, buf = _reply_to(buf, 1)
                if msg is not None:
                    return done(True, *_describe_answer(msg))
            return done(
                False,
                f"`{c.command}` exited ({proc.poll()}) without answering `initialize`."
                + stderr_tail(),
            )
        finally:
            _stop(proc)


def verify(
    repo: Path, ids: list[str] | None = None, *, timeout_s: float = VERIFY_TIMEOUT_S
) -> list[Verification]:
    """Launch every MCP companion (or just `ids`) and check it speaks MCP, concurrently.

    NEVER called from `scan`. Concurrent because each launch may be an `npx` cold start.
    """
    from concurrent.futures import ThreadPoolExecutor

    comps = [c for c in load(repo) if c.is_mcp and (ids is None or c.id in ids)]
    if not comps:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(comps))) as ex:
        return list(ex.map(lambda c: verify_one(c, timeout_s=timeout_s), comps))


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


#: An npm package spec with a version suffix: `pkg@latest`, `@scope/pkg@1.2.3`, `pkg@^2`.
#: The name part is npm's own charset (no `:` or `/` beyond the scope), and the rule runs
#: only for an npm launcher (`NPM_LAUNCHERS`): run on every argument, it read the host in
#: `postgresql://u:p@1db.example` as a version and made two databases one server.
_NPM_TAG = re.compile(
    r"^((?:@[a-z0-9][\w.-]*/)?[a-z0-9][\w.-]*)@(?:latest|next|[\^~]?v?\d[\w.+-]*)$"
)

#: Launchers whose arguments name an npm package, where a version tag is not identity.
NPM_LAUNCHERS = frozenset({"npx", "npm", "pnpm", "pnpx", "bunx", "yarn"})


def _norm_args(cmd: str, args: list[str]) -> list[str]:
    if Path(cmd).name not in NPM_LAUNCHERS:
        return list(args)
    return [m.group(1) if (m := _NPM_TAG.match(a)) else a for a in args]


def _launch_of(entry: object) -> tuple[str, list[str]] | None:
    """``(command, args)`` of one stored server entry, whatever the agent's shape.

    opencode/Kilo keep one `command` ARRAY holding the arguments; a few configs write the
    whole launch as one `command` string. A remote server (`url`) has no launch.
    """
    if not isinstance(entry, dict):
        return None
    cmd, args = entry.get("command"), entry.get("args") or []
    if not isinstance(args, list):
        return None  # checked BEFORE the array form merges it: `*5` raised, `*"a b"` split
    if isinstance(cmd, list) and cmd and all(isinstance(x, str) for x in cmd):
        # Already tokenised: an element holding a space is ONE token (`/Apps/My App/x`).
        return (cmd[0], [*cmd[1:], *map(str, args)]) if cmd[0].strip() else None
    if not isinstance(cmd, str) or not cmd.strip():
        return None
    # A whole launch written as one string is split -- unless the string names a file
    # that exists, which is a path with a space in it, not a command line.
    if not args and any(ch.isspace() for ch in cmd.strip()) and not Path(cmd).exists():
        try:
            cmd, *args = shlex.split(cmd)
        except ValueError:
            return None
    return cmd, [str(a) for a in args]


def launches_as(c: Companion, entry: object) -> bool:
    """Does this stored server entry launch companion ``c``?

    How strict "the same launch" is, decided once:

    * the same COMMAND, compared by basename (`/usr/local/bin/npx` is `npx`);
    * the companion's ARGUMENTS appear in the entry IN ORDER, with only flags (and the
      value of a flag known to take one, `VALUE_FLAGS`) between them -- `docker run --rm -i -e TOKEN <image>` is still that
      image, `docker run <other-image> <image>` is not -- and anything after them;
    * an npm version tag is ignored on both sides (`@upstash/context7-mcp@latest`),
      for an npm launcher only -- elsewhere `x@1.2` is not a package and not a tag;
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
    want = _norm_args(c.command, c.args)
    have = _norm_args(cmd, args)
    if not want:
        return not have
    return _in_order_with_flags(want, have, VALUE_FLAGS.get(Path(cmd).name, frozenset()))


#: Flags known to take their value as the NEXT argument, per launcher. Whether `-x v`
#: is a flag and its value or a boolean flag and a positional cannot be told from the
#: tokens, and guessing "value" let `docker run -i <other-image> <image>` pass as the
#: companion. So only these consume a following token; any other flag is taken as
#: boolean, and a value written `--flag=value` needs no entry here.
_DOCKER_VALUE_FLAGS = frozenset(
    {"-a", "--attach", "--add-host", "--annotation", "--blkio-weight", "--blkio-weight-device", "-c", "--cap-add", "--cap-drop",
     "--cgroup-parent", "--cgroupns", "--cidfile", "--cpu-count", "--cpu-percent",
     "--cpu-period", "--cpu-quota", "--cpu-rt-period", "--cpu-rt-runtime", "--cpu-shares",
     "--cpus", "--cpuset-cpus", "--cpuset-mems", "--detach-keys",
     "--device-read-bps", "--device-read-iops", "--device-write-bps", "--device-write-iops",
     "--device", "--device-cgroup-rule", "--dns", "--dns-opt", "--dns-option", "--dns-search",
     "--domainname", "-e", "--entrypoint", "--env", "--env-file", "--expose", "--gpus",
     "--group-add", "-h", "--health-cmd", "--health-interval", "--health-retries",
     "--health-start-interval", "--health-start-period", "--health-timeout", "--hostname",
     "--ip", "--ip6", "--ipc", "--kernel-memory", "--link-local-ip",
     "--isolation", "-l", "--label", "--label-file", "--link", "--log-driver", "--log-opt",
     "-m", "--mac-address", "--memory", "--memory-reservation", "--memory-swap",
     "--memory-swappiness", "--network-alias", "--pids-limit", "--mount", "--name", "--net",
     "--network", "--oom-score-adj", "-p", "--pid", "--platform", "--publish", "--pull",
     "--restart", "--runtime", "--security-opt", "--shm-size", "--stop-signal",
     "--stop-timeout", "--storage-opt", "--sysctl", "--tmpfs", "-u", "--ulimit", "--user",
     "--userns", "--uts", "-v", "--volume", "--volume-driver", "--volumes-from", "-w", "--workdir"}
)  # fmt: skip
VALUE_FLAGS: dict[str, frozenset[str]] = {
    "docker": _DOCKER_VALUE_FLAGS,
    "podman": _DOCKER_VALUE_FLAGS,
    # Not npx: its `-p <pkg>` value IS the package that names the server, so consuming it
    # as a flag's value hid the one token the match needs. Taken as a flag, `-p` is
    # skipped and the package is compared like any other argument.
}


def _in_order_with_flags(want: list[str], have: list[str], value_flags: frozenset[str]) -> bool:
    """``want`` appears in ``have`` in order, with only FLAGS between its items.

    Between two of the companion's arguments the entry may add a flag (`-i`, `--x=y`),
    and a flag in ``value_flags`` may bring the one value after it (`-e TOKEN`). Any
    other bare positional there is another package or image, and the companion's own
    name after it is only that server's argument (`npx -y server-github
    server-filesystem` launches github). Once every item of ``want`` is matched, the
    rest is the server's own configuration and is allowed.
    """
    i, takes_value = 0, False
    for h in have:
        if i == len(want):
            return True
        if takes_value:
            takes_value = False
        elif h == want[i]:
            i += 1
        elif h.startswith("-"):
            takes_value = h in value_flags
        else:
            return False
    return i == len(want)


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
            # Unreadable, like invalid JSON: the agent's own parser rejects the file, so
            # nothing in it launches -- a header matched as text counted it registered.
            return None
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


#: The keys agents use for a REMOTE server's address: Claude/Cursor/VS Code `url`, Gemini
#: CLI `httpUrl`, Windsurf `serverUrl`. A remote server has no launch and is still one.
_REMOTE_KEYS = ("url", "httpUrl", "serverUrl")


def _serves(entry: object) -> bool:
    """Does one stored entry start or reach SOME server -- a launch, or a remote address?

    What it serves is not asked: an operator's own wrapper under the companion's id is
    theirs. Only an entry that can serve nothing -- `{}`, `"x"`, `5`, `[]`, a `null`
    placeholder -- is refused, because counting it reported the gate as covered while no
    agent could reach the tool, and `register` would have written over it (B768503a43a).
    """
    if _launch_of(entry) is not None:
        return True
    return isinstance(entry, dict) and any(
        isinstance(entry.get(k), str) and entry[k].strip() for k in _REMOTE_KEYS
    )


def _registered_name(servers: dict, text: str, shape: str, c: Companion | str) -> str | None:
    """The name one config launches ``c`` under: its id first, else a matching launch."""
    cid = c if isinstance(c, str) else c.id
    if cid in servers and _serves(servers[cid]):
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


def _launched_elsewhere(read: tuple[dict, str] | None, c: Companion, rel: str) -> str:
    """The message when config ``rel`` already launches ``c`` under ANOTHER name, else "".

    Writing the id beside it would start the same server twice, under two names, with
    two copies of every tool -- and an operator whose tools are already named after
    their own key (`mcp__coding-guides__*`) would get a second set they never asked for.
    """
    servers = read[0] if read else {}
    for name, entry in servers.items():
        if name != c.id and launches_as(c, entry):
            msg = f"{rel} already launches {c.id} as `{name}` (the same command); left as it is"
            # An entry under the id too is not repaired here: refreshing it would be a
            # second copy, deleting it is the operator's call. But it is said -- and only
            # what was CHECKED: an id entry differing by `env` launches the same server.
            if c.id in servers and launches_as(c, servers[c.id]):
                msg += f". `{c.id}` launches it too: two copies, remove one by hand"
            elif c.id in servers:
                msg += f". The entry under `{c.id}` is not this launch: remove it by hand"
            return msg
    return ""


def _toml_without(text: str, cid: str) -> str | None:
    """``text`` minus every ``[mcp_servers.<cid>]`` table and sub-table, or None.

    Textual, since `tomllib` cannot write. The cut is trusted only when the result still
    parses and differs from the original by exactly that server: an inline table, a dotted
    key, or a header inside a multi-line string fails the check and is left alone.
    """
    name = "(?:{0}|\"{0}\"|'{0}')".format(re.escape(cid))
    header = re.compile(rf"^\s*\[\s*mcp_servers\s*\.\s*{name}\s*(?:\.[^\]]*)?\]\s*(?:#.*)?$")
    kept: list[str] = []
    cut: list[str] = []  # the lines of the table being dropped
    for line in text.splitlines(keepends=True):
        if header.match(line):
            cut.append(line)
        elif cut and line.lstrip().startswith("["):
            # Comments and blank lines just above the NEXT header describe it, not the
            # table that is going: keep them.
            tail = []
            while cut[-1].strip() == "" or cut[-1].lstrip().startswith("#"):
                tail.insert(0, cut.pop())
            kept.extend(tail)
            cut = []
            kept.append(line)
        elif cut:
            cut.append(line)
        else:
            kept.append(line)
    out = "".join(kept)
    try:
        before, after = tomllib.loads(text), tomllib.loads(out)
    except tomllib.TOMLDecodeError:
        return None
    srv = before.get("mcp_servers")
    if not isinstance(srv, dict):
        return None
    expect = {**before, "mcp_servers": {k: v for k, v in srv.items() if k != cid}}
    # A bare `[mcp_servers]` header is an empty table on both sides, or on neither.
    for d in (expect, after):
        if d.get("mcp_servers") == {}:
            d.pop("mcp_servers")
    return out if after == expect else None


def _toml_stale_entry(text: str, c: Companion) -> bool:
    """Does the table under the id launch something that is not ``c``'s launch?"""
    try:
        servers = tomllib.loads(text).get("mcp_servers", {})
    except tomllib.TOMLDecodeError:
        return False
    if not isinstance(servers, dict) or c.id not in servers:
        return False
    entry = servers[c.id]
    return _serves(entry) and not launches_as(c, entry)


def _toml_present(text: str, new_text: str, c: Companion, rel: str) -> tuple[str, str] | None:
    """What a TOML config already says about ``c``, or None when ``new_text`` may be written.

    The same judgement the reader makes (`_registered_name`): an entry under the id counts
    only when it launches something, and a launch under another name counts too. Anything
    else is appended -- but only when the result still PARSES. A table under the id that
    launches nothing, or an `mcp_servers` that is not a table, would turn the append into
    a file the agent rejects whole; that is refused by name, never reported as already
    registered (B768503a43a). Nor is anything appended to a file that does not parse.
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return "refused", f"SKIPPED {rel}: it is not valid TOML; add {c.id} by hand"
    servers = data.get("mcp_servers", {})
    if not isinstance(servers, dict):
        # `mcp_servers = 5`, or `[[mcp_servers]]`: an appended header would either break
        # the file or land inside the last array element, where no agent reads it.
        return "refused", f"SKIPPED {rel}: its `mcp_servers` is not a table; add {c.id} by hand"
    if c.id in servers and _serves(servers[c.id]):
        if launches_as(c, servers[c.id]):
            return "unchanged", f"{rel} already registers {c.id}"
    if other := _launched_elsewhere((servers, text), c, rel):
        return "unchanged", other
    try:
        tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as exc:
        what = (
            f"[mcp_servers.{c.id}] is there but launches nothing"
            if c.id in servers
            else f"adding [mcp_servers.{c.id}] would not parse ({exc})"
        )
        return "refused", f"SKIPPED {rel}: {what}; fix it by hand"
    return None


def _register_toml(path: Path, rel: str, c: Companion, dry_run: bool) -> tuple[str, str]:
    """The TOML (codex) half of `register`."""
    text = path.read_text("utf-8") if path.exists() else ""
    block = f"\n[mcp_servers.{c.id}]\ncommand = {_toml(c.command)}\nargs = {_toml(list(c.args))}\n"
    if c.env:
        block += f"env = {_toml(dict(c.env))}\n"
    replaced = False
    if _toml_stale_entry(text, c):
        # Refreshed like the JSON path (B662a1ace82): the old table is cut out and the
        # registry's launch appended -- unless the same launch already runs under
        # another name (a second copy), or the table is not one that can be cut out.
        if other := _launched_elsewhere((tomllib.loads(text)["mcp_servers"], text), c, rel):
            return "unchanged", other
        if (cut := _toml_without(text, c.id)) is None:
            return "refused", (
                f"SKIPPED {rel}: [mcp_servers.{c.id}] launches something other than the "
                f"registry's launch and is not a plain table that can be rewritten; "
                f"replace it by hand"
            )
        text, replaced = cut, True
    new_text = text.rstrip() + "\n" + block if text.strip() else block.lstrip()
    if verdict := _toml_present(text, new_text, c, rel):
        return verdict
    if dry_run and replaced:
        return "written", f"WOULD replace in {rel}:\n{block.lstrip()}"
    if dry_run:
        # The block as it will be APPENDED, minus the leading blank line that only
        # separates it from what is above. `test_the_preview_matches_the_write_for_a
        # _TOML_target_too` asserts this text appears verbatim in the written file,
        # which is the guarantee that matters; showing the whole merged file here
        # would bury one added stanza in the operator's entire config.
        return "written", f"WOULD add to {rel}:\n{block.lstrip()}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, "utf-8")
    return "written", f"{'refreshed' if replaced else 'registered'} {c.id} in {rel}"


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
            f"settings as server `{c.id}`, in that agent's own config format, launched "
            f"with: {json.dumps(c.entry())}"
        )
    rel = target.config
    path = Path(repo) / rel

    # ONE decision, made once, for both paths. The dry run used to re-derive the entry
    # by copy-paste, so the two already disagreed: the TOML preview omitted the leading
    # newline the write prepends, and the JSON preview happily reported "WOULD add"
    # over a file the write would REFUSE as unparseable -- an operator signing off on a
    # change that could not happen, which is the failure the preview exists to prevent.
    if target.shape == SHAPE_TOML:
        return _register_toml(path, rel, c, dry_run)

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
    # Checked even when the id holds a STALE entry: refreshing it to this launch would
    # start the server twice, once under each name. The other name already serves it.
    if other := _launched_elsewhere((get_servers(data, target.shape), ""), c, rel):
        return "unchanged", other
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
    see `BUILTIN_COVERAGE`, whose names are not companion ids.

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
