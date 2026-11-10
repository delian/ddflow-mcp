"""The interface census (ADP-census-v2): what `archdeps` cannot see because it is not an import.

`archdeps` (ADP-census) counts import families. This module counts five kinds of interface a
Python import never shows -- ``program`` (external programs run: argv[0] literals and
``shutil.which`` targets), ``protocol`` (MCP / ACP / JSON-RPC spoken by hand), ``managed-file``
(files other tools own and ddflow edits), ``env`` (environment variables read) and
``network`` (network calls and hard-coded URL hosts) -- so ADP-guard-final can prove that every
external interface, not only every import, goes through its adapter.

A family's *home* is the planned ADP-* task that puts it behind a port; its *home modules*
are the ddflow modules already allowed to touch it (empty while no adapter exists), and a
*violation* is a call site outside them. The ratchet counts violations per (kind, family,
file): a NEW key or a grown count fails, a shrunk one asks for a lower baseline, so the
committed baseline (``tests/arch_baselines/arch_interfaces.txt``) only shrinks and survives a
line shift. The scan is the AST of ``repo/ddflow`` (`archifaces_scan`), seconds, no execution.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path

from .archdeps import STDLIB_HOMES, _dotted
from .archifaces_scan import (  # noqa: F401  (re-exported)
    ACP_ONLY_AGENTS,
    ALL_ENV,
    DYNAMIC,
    PLATFORM,
    TOOLS,
    Site,
    agent_clis,
    sites_in,
)

# Where the committed ratchet baseline lives, relative to the repository root.
BASELINE_PATH = "tests/arch_baselines/arch_interfaces.txt"
# The census's own modules hold the pattern tables (method names, file names, URLs) as string
# literals, data and not use: their LITERAL kinds are left out (counting them would make every
# edit of a table a "new call site"); their calls (programs, env reads, sockets) still count.
SELF_MODULES = ("ddflow.services.archifaces", "ddflow.services.archifaces_scan")
KINDS = ("program", "protocol", "managed-file", "env", "network")
_RUNNER = ("ddflow.infra.proc", "ddflow.infra.git")

# (planned home task, modules already allowed) per program family; the default is ADP-tools.
PROGRAM_HOMES: dict[str, tuple[str, tuple[str, ...]]] = {
    "git": ("ADP-git", ("ddflow.infra.git",)),
    "gh": (
        "ADP-forge",
        ("ddflow.infra.forge", "ddflow.infra.upstream_gh", "ddflow.adapters.forge"),
    ),
    "glab": (
        "ADP-forge",
        ("ddflow.infra.forge", "ddflow.adapters.forge", "ddflow.adapters.forge_gitlab"),
    ),
    DYNAMIC: ("ADP-tools", _RUNNER),
    **dict.fromkeys(TOOLS, ("ADP-tools", ())),
    **dict.fromkeys(PLATFORM, ("ADP-platform", ())),
    "sh": ("ADP-platform", _RUNNER),  # a shell entry point of the process runner itself
}
KIND_HOMES: dict[str, str] = {"managed-file": "ADP-managed-files", "env": "ADP-clock-env"}
PROTOCOL_HOMES: dict[str, str] = {
    "mcp": "ADP-mcp",
    "acp": "ADP-acp",
    "jsonrpc": "ADP-lane-transport",
}


# Modules already allowed to speak a protocol by hand: the adapter and its fake (ADP-mcp).
PROTOCOL_MODULES: dict[str, tuple[str, ...]] = {
    "mcp": ("ddflow.adapters.mcp", "ddflow.adapters.fakes.mcp"),
    "acp": ("ddflow.adapters.acp", "ddflow.adapters.fakes.acp"),
}


def home_of(kind: str, family: str) -> tuple[str, tuple[str, ...]]:
    """The planned ADP-* task and the modules already allowed for one family."""
    if kind == "program":
        if family in agent_clis():
            return ("ADP-agent-lanes", ())
        return PROGRAM_HOMES.get(family, ("ADP-tools", ()))
    if kind == "protocol":
        return PROTOCOL_HOMES.get(family, "ADP-lane-transport"), PROTOCOL_MODULES.get(family, ())
    if kind == "network":  # the import census already declares the http home: one list
        return "ADP-http", STDLIB_HOMES.get("http", ())
    return KIND_HOMES.get(kind, "ADP-http"), ()


@dataclass(frozen=True)
class InterfaceFamily:
    """One (kind, family) row: its call sites, planned home task and allowed modules."""

    kind: str
    family: str
    home: str
    home_modules: tuple[str, ...]
    sites: tuple[Site, ...]

    @property
    def violations(self) -> tuple[Site, ...]:
        return tuple(s for s in self.sites if s.module not in self.home_modules)


@dataclass(frozen=True)
class InterfaceCensus:
    families: tuple[InterfaceFamily, ...]
    unparsed: tuple[str, ...] = ()


def scan(repo: Path) -> InterfaceCensus:
    """The interface census of ``repo/ddflow``, families sorted by kind then name."""
    pkg = repo / "ddflow"
    if not pkg.is_dir():
        return InterfaceCensus(())
    by_key: dict[tuple[str, str], list[Site]] = {}
    unparsed: list[str] = []
    for path in sorted(
        p for p in pkg.rglob("*.py") if "__pycache__" not in p.relative_to(repo).parts
    ):
        module = _dotted(repo, path)
        try:
            tree = ast.parse(path.read_bytes())
        except (OSError, SyntaxError, ValueError):
            unparsed.append(module)
            continue
        rel = path.relative_to(repo).as_posix()
        for s in sites_in(
            tree,
            module,
            rel,
            is_init=path.name == "__init__.py",
            literals=module not in SELF_MODULES,
        ):
            by_key.setdefault((s.kind, s.family), []).append(s)
    rows = []
    for (kind, family), sites in sorted(
        by_key.items(), key=lambda kv: (KINDS.index(kv[0][0]), kv[0][1])
    ):
        home, modules = home_of(kind, family)
        rows.append(InterfaceFamily(kind, family, home, modules, tuple(sites)))
    return InterfaceCensus(tuple(rows), tuple(sorted(unparsed)))


# -- the ratchet ------------------------------------------------------------------------
Key = tuple[str, str, str]


def counts(census: InterfaceCensus) -> dict[Key, int]:
    """Violation call sites per (kind, family, path): the quantity the ratchet bounds."""
    out: dict[Key, int] = {}
    for f in census.families:
        for s in f.violations:
            key = (f.kind, f.family, s.path)
            out[key] = out.get(key, 0) + 1
    return out


def format_baseline(census: InterfaceCensus) -> str:
    """The committed baseline text: one JSON ``[kind, family, path, count]`` per line, sorted
    (JSON so that any file name round-trips)."""
    if census.unparsed:  # a baseline written now would drop what those files hold
        raise ValueError(f"cannot write a baseline: unparsed {', '.join(census.unparsed)}")
    rows = sorted(counts(census).items())
    return "".join(json.dumps([k, fam, p, n]) + "\n" for (k, fam, p), n in rows)


def parse_baseline(text: str) -> dict[Key, int]:
    out: dict[Key, int] = {}
    for raw in text.split("\n"):
        line = raw.strip()
        if line and not line.startswith("#"):
            kind, family, path, n = json.loads(line)
            out[(kind, family, path)] = int(n)
    return out


def ratchet(
    census: InterfaceCensus, baseline: dict[Key, int]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(grew, shrank): sites NEW or more than baselined, and baseline entries now too high."""
    now = counts(census)
    # a file that stopped parsing is reported in `unparsed`; its sites did not go away
    broken = {m.replace(".", "/") + ext for m in census.unparsed for ext in (".py", "/__init__.py")}
    # fail closed: a file the census could not read is an unknown, not an absence
    grew: list[str] = [f"unparsed {m}: the census cannot read it" for m in census.unparsed]
    shrank: list[str] = []
    for key in sorted(now):
        was = baseline.get(key, 0)
        if now[key] > was:
            grew.append(f"{key[0]} {key[1]} in {key[2]}: {now[key]} site(s), baseline {was}")
    for key in sorted(baseline):
        if now.get(key, 0) < baseline[key] and key[2] not in broken:
            shrank.append(
                f"{key[0]} {key[1]} in {key[2]}: {now.get(key, 0)} site(s), baseline {baseline[key]}"
            )
    return tuple(grew), tuple(shrank)
