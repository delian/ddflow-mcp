"""Registration, the probe cache, ``scan``, ``register`` and the coverage views.

Split out of ``ddflow/services/companions.py``: reading which agents already launch a
companion (``registrations`` / ``registered_in``), the disposable probe cache, the one
entry point every surface actually calls (``scan``), the one that writes config
(``register``), and the coverage views the brief and the handshake both judge by
(``coverage_gates`` / ``gate_coverage`` / ``gate_gaps``). It sits above ``.match``
(whether a stored entry launches a companion) and ``.model`` (what a companion IS).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from ...config import Config
from ...infra.fsio import atomic_write
from .. import mcpconfig as MC
from ..adopt import AGENT_TARGETS, SHAPE_TOML, get_servers
from ..gates import pipelines
from ..mcpconfig import (
    SHAPE_CORDIS_PATCH,
    _declared,
    _load_toml,
    cordis_rows,
    cordis_servers,
)
from .match import launches_as
from .model import Companion, Status, load


def _servers_in(path: Path, shape: str) -> tuple[dict, str] | None:
    """(servers by name, raw text) of one agent config; None when unreadable."""
    try:
        text = path.read_text("utf-8")
    except (UnicodeDecodeError, OSError):  # not UTF-8 is unreadable too (B8bd68c2e6c)
        return None
    if shape == SHAPE_TOML:
        data = _load_toml(text)
        if data is None:
            # Unreadable, like invalid JSON: the agent's own parser rejects the file, so
            # nothing in it launches -- a header matched as text counted it registered.
            return None
        servers = data.get("mcp_servers") or {}
        return (servers if isinstance(servers, dict) else {}), text
    if shape == SHAPE_CORDIS_PATCH:
        rows = cordis_rows(text)
        if rows is None:
            return None  # a tagged line that is not JSON: unreadable, not empty
        return cordis_servers(rows), text
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


def _launched_name(servers: dict, c: Companion, *, skip_id: bool = False) -> str | None:
    """The name whose entry's launch is ``c``'s, or None.

    `_registered_name` (what an agent DOES launch) and `_launched_elsewhere` (the second
    copy a `register` must not add) both ask this of the same table; `skip_id` is the
    writer's extra question -- is it launched under a name OTHER than the id.
    """
    for name, entry in servers.items():
        if skip_id and name == c.id:
            continue
        if launches_as(c, entry):
            return name
    return None


def _registered_name(servers: dict, text: str, shape: str, c: Companion | str) -> str | None:
    """The name one config launches ``c`` under: its id first, else a matching launch."""
    cid = c if isinstance(c, str) else c.id
    if _declared(servers, cid):
        return cid
    if isinstance(c, str):
        return None
    return _launched_name(servers, c)


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


def _read_cache(repo: Path, ttl_s: int) -> dict[str, tuple[bool, str, float]]:
    """Cached probe results still inside the TTL, each with WHEN it was probed.
    Unreadable cache = no cache."""
    if ttl_s <= 0:
        return {}
    try:
        raw = json.loads(_cache_path(repo).read_text("utf-8"))
        cutoff = time.time() - ttl_s
        return {
            cid: (bool(e["installed"]), str(e["detail"]), float(e["at"]))
            for cid, e in raw.items()
            if isinstance(e, dict) and float(e.get("at", 0)) >= cutoff
        }
    except (OSError, ValueError, KeyError, TypeError):
        # A corrupt cache is a cache miss, never an error. It is derived data whose
        # only job is to be faster than asking again.
        return {}


def _write_cache(repo: Path, fresh: dict[str, tuple]) -> None:
    """Each entry with its OWN probe time -- ``(installed, detail, at)``, or without
    ``at`` for one probed just now: one stamped `now` on every write never expired while
    any other companion was probed inside the TTL (Bc87ca3936a). Replaced atomically, so a
    concurrent reader never sees half a file."""
    path = _cache_path(repo)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        merged = {
            # the entry's own `at` when it carries one, else now
            cid: {"installed": e[0], "detail": e[1], "at": (*e[2:], now)[0]}
            for cid, e in fresh.items()
        }
        atomic_write(path, json.dumps(merged, indent=2), fsync=False)
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
        ttl_s = Config.load(repo).companions.probe_cache_ttl_s
    from . import (
        is_installed as _is_installed,  # late-bound: honours monkeypatch of the package attr
    )

    cached = _read_cache(repo, ttl_s) if probe else {}
    out, fresh = [], dict(cached)
    for c in load(repo):
        if not probe:
            inst, detail = None, "not probed"
        elif c.id in cached:
            inst, detail, _at = cached[c.id]
        else:
            inst, detail = _is_installed(c)
            if inst is not None:
                fresh[c.id] = (inst, detail, time.time())
        regs = registrations(repo, c)
        out.append(Status(c, inst, list(regs), detail, regs))
    if probe and ttl_s > 0 and fresh != cached:
        _write_cache(repo, fresh)
    return out


def _launched_elsewhere(read: tuple[dict, str] | None, c: Companion, rel: str) -> str:
    """The message when config ``rel`` already launches ``c`` under ANOTHER name, else "".

    Writing the id beside it would start the same server twice, under two names, with
    two copies of every tool -- and an operator whose tools are already named after
    their own key (`mcp__coding-guides__*`) would get a second set they never asked for.
    """
    servers = read[0] if read else {}
    name = _launched_name(servers, c, skip_id=True)
    if name is None:
        return ""
    msg = f"{rel} already launches {c.id} as `{name}` (the same command); left as it is"
    # An entry under the id too is not repaired here: refreshing it would be a
    # second copy, deleting it is the operator's call. But it is said -- and only
    # what was CHECKED: an id entry differing by `env` launches the same server.
    if c.id in servers and launches_as(c, servers[c.id]):
        msg += f". `{c.id}` launches it too: two copies, remove one by hand"
    elif c.id in servers:
        msg += f". The entry under `{c.id}` is not this launch: remove it by hand"
    return msg


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
    # ONE decision, made once, for both paths and for the preview: `mcpconfig.register`,
    # the writer `adopt` uses for ddflow itself. What is companion-specific is passed in:
    # a TOML entry is this companion when it LAUNCHES it (`launches_as`), and a launch
    # under another name is a second copy not to write (`_launched_elsewhere`).
    return MC.register(
        Path(repo) / rel,
        target.shape,
        c.id,
        c.entry(),
        dry_run=dry_run,
        rel=rel,
        same=lambda stored: launches_as(c, stored),
        elsewhere=lambda servers: _launched_elsewhere((servers, ""), c, rel),
    )


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


def coverage_gates(cfg) -> list[str]:
    """Every gate a pipeline that runs here names, in pipeline order, once: what the
    coverage views judge (B726755d8f7). Read off `gates.pipelines`, as `ddflow workflow`
    reads it, rather than naming pipelines -- naming task alone dropped the promotion
    pipeline, and a list would drop the next one the same way. `running`: a promotion
    pipeline with no `flow.environments` advertises gates nothing passes (Bc0cd05d0c5).
    """
    out: list[str] = []
    for ids in pipelines(cfg, running=True).values():
        out.extend(g for g in ids if g not in out)
    return out


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


def gate_gaps(cover: dict[str, list[str]], statuses: list[Status]) -> list[str]:
    """Gates with no companion behind them, from `gate_coverage`.

    A gate is only a GAP if every companion that could serve it is known absent: one whose
    install state is unknown (``usable is None``, an unprobed cli tool) leaves its gates
    unjudged, since "we did not look" is not "nothing is there" -- reporting `rules` as
    unserved on the strength of not having checked told the agent a gate had nothing
    behind it with the tool on the PATH. The one definition behind `companions`
    (``uncovered_gates``) and the MCP handshake (``gate_gaps``).
    """
    unknown = {g for st in statuses if st.usable is None for g in st.companion.gates}
    return [g for g, ids in cover.items() if not ids and g not in unknown]
