"""The companion model, the shipped registry, and read-only detection.

Split out of ``ddflow/services/companions.py``: what a companion IS (``Companion``,
``Status``) and the parts that read a config file or run a bounded ``--version`` /
``--help`` probe rather than launch anything. Launching a server and speaking MCP is
``.probe``; matching a stored MCP entry to a companion is ``.match``; reading and
writing registrations, the probe cache, ``scan``, ``register`` and the coverage views
is ``.registry``.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ...config import _is_code_tree
from ...infra import paths, tomlcfg
from ...infra import proc as P

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


#: The wording for each advice a surface can act on. ONE map: the MCP handshake and the
#: SessionStart brief both say these words, and two copies is how they drift.
ADVICE_WORDS: dict[str, str] = {
    "register": "installed, not registered",
    "install": "not installed",
    "check": "not checked",
}


def actionable(statuses: list[Status], *, grouped: bool = False) -> list[tuple[Status, str]]:
    """Every DEFAULT companion with something to do about it, with its state word.

    Scan order by default; `grouped=True` orders register, install, check (the handshake's
    order, which puts the cheapest remedy first)."""
    rows = [
        (s, ADVICE_WORDS[s.advice])
        for s in statuses
        if s.companion.default and s.advice in ADVICE_WORDS
    ]
    if grouped:
        order = list(ADVICE_WORDS)
        rows.sort(key=lambda r: order.index(r[0].advice))  # stable within a group
    return rows


def load(repo: Path) -> list[Companion]:
    """The shipped registry, overlaid by the project's own.

    A project entry with an existing id REPLACES it (so a project can point `roborev`
    at its own wrapper); a new id is appended. Reads `.ddflow/config.toml` as well as
    `.ddflow/companions.toml`, through the same loader gates and reviewers use — and
    therefore with the same policy: **an unknown field is an error** in the tree the
    code came from (elsewhere the file may be newer than the code: skipped with a
    warning, B0016a65167). It used to drop them silently, so a misspelt `commmand` produced a companion with no command that
    `companions add` would write into an agent's config as a launch line failing
    mid-task.
    """
    out: dict[str, Companion] = {}
    shipped = paths.templates_dir() / "companions.toml"
    sources = [shipped, *tomlcfg.config_paths(repo, "companions.toml")]
    # A newer checkout's companion field warns and is skipped by older code (B0016a65167).
    lenient = not _is_code_tree(Path(repo))
    for cid, spec in tomlcfg.overlay_array(
        sources, "companion", Companion, key="id", lenient=lenient
    ).items():
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
    p = P.capture(c.detect, timeout=DETECT_TIMEOUT_S)
    if p.timed_out:
        return None, (
            f"`{' '.join(c.detect)}` did not answer within {DETECT_TIMEOUT_S}s — could "
            f"not tell. Not the same as absent: re-run, or check it by hand."
        )
    if p.error is not None:
        return None, f"the probe could not be run at all ({p.error}) — could not tell"
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
