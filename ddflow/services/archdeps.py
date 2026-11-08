"""The dependency census (ADP-census): every external dependency family, its importing
modules, its declared home and the violations -- the report that drives one home contract
per family (R-adapter-boundaries) and the proof ADP-guard-final later builds on.

A *family* is one external dependency, either a third-party package (jinja2, radon, ...)
or one of the OS-level stdlib families the adapter work pulls behind a port: http
(urllib.request / http.client / socket), sqlite3, tomllib, shutil, subprocess,
tempfile/fcntl (fsio), hashlib, and the clock/env/randomness/ids family (time / uuid /
random / os.environ). A family's *declared home* is the ddflow module allowed to import
it; every other importer is a *violation*. The census is counted on the source with
``ast``, never by running anything, so it runs in seconds. The stdlib set is the
running interpreter's `sys.stdlib_module_names`, which is per-platform: a module the tree
imports only on another OS (``winreg``/``msvcrt`` on Windows) is stdlib THERE but not here,
so what reads as third-party depends on the platform the scan runs on.

Third-party packages are detected dynamically (an external module that is not in the
running interpreter's stdlib and not ddflow), so a NEW third-party package is a NEW
family -- which the ratchet test fails until it is given a declared home.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

# The stdlib family taxonomy: family name -> the root module names that make the family.
# Structural, not a tunable: it is the enumeration the whole adapter migration ticks off
# (R-adapter-boundaries), one family per ADP-* task.
STDLIB_FAMILIES: dict[str, tuple[str, ...]] = {
    "http": ("urllib", "http", "socket"),
    "sqlite3": ("sqlite3",),
    "tomllib": ("tomllib",),
    "shutil": ("shutil",),
    "subprocess": ("subprocess",),
    "fsio": ("tempfile", "fcntl"),
    "hashlib": ("hashlib",),
    "clock-env": ("time", "uuid", "random", "os"),
}
# A family is keyed on the IMPORT ROOT: ``os`` above stands for ``os.environ`` and ``urllib``
# for the network family, so a module importing ``os`` only for ``os.path``, or ``urllib``
# only for ``urllib.parse``, is still listed under the family (the root is all the census
# can see). That over-reporting is documented, not a defect, and each family's finer split
# is that family's own ADP-* port, not the census's job.

# A stdlib family's declared home: the ddflow module(s) allowed to import any of its
# members. A family absent here has no home contract yet -- every current importer is the
# baseline, and the ADP-* task that moves the family behind a port declares a home here.
STDLIB_HOMES: dict[str, tuple[str, ...]] = {
    "subprocess": ("ddflow.adapters.proc", "ddflow.adapters.proc_server"),
    "fsio": ("ddflow.adapters.fsio",),
    "shutil": ("ddflow.infra.fsio",),
    "hashlib": ("ddflow.adapters.digest",),
    "http": ("ddflow.adapters.http",),
    "tomllib": ("ddflow.adapters.toml",),
}

# A third-party package's declared home: the one module allowed to import the package.
THIRD_PARTY_HOMES: dict[str, tuple[str, ...]] = {
    "rapidfuzz": ("ddflow.core.fuzzy",),
    "model2vec": ("ddflow.services.embed",),
    # ADP-toml made `ddflow.adapters.toml` the single wrapper for TOML I/O: `tomllib` to
    # read and `tomlkit` to write (`TomlLibIo.dumps`/`document`). Until `infra.tomlcfg`
    # delegates to it (ADP-toml-unify), BOTH are the declared home for tomlkit.
    "tomlkit": ("ddflow.infra.tomlcfg", "ddflow.adapters.toml"),
}


@dataclass(frozen=True)
class FamilyReport:
    """One family's census row. ``importers`` is every ddflow module that imports any
    member; ``violations`` is the importers not in ``home`` (all of them when no home is
    declared)."""

    family: str
    members: tuple[str, ...]
    home: tuple[str, ...]
    importers: tuple[str, ...]
    third_party: bool = False

    @property
    def violations(self) -> tuple[str, ...]:
        home = frozenset(self.home)
        return tuple(m for m in self.importers if m not in home)


@dataclass(frozen=True)
class Census:
    """The whole census, families in a stable order (stdlib families first, then
    third-party packages by name). ``unparsed`` holds every ``ddflow`` module whose
    file could not be read or parsed, so a bad file is reported, never skipped."""

    families: tuple[FamilyReport, ...]
    unparsed: tuple[str, ...] = ()


class ArchDepsCensus(Protocol):
    """The port: scan a repository's ``ddflow`` package and report every family."""

    def scan(self, repo: Path) -> Census:
        """Return the census of ``repo / "ddflow"``."""
        ...


def _dotted(repo: Path, path: Path) -> str:
    parts = list(path.relative_to(repo).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _external_roots(path: Path) -> tuple[str, ...] | None:
    """The root module names ``path`` imports from outside ddflow: the first component of
    every ``import x.y`` and every level-0 ``from x.y import ...``, sorted and unique.

    ``None`` when the file cannot be read or parsed: an unparsable file is REPORTED (in
    ``Census.unparsed``), never silently treated as importing nothing, or a new import
    inside it would be invisible to the ratchet."""
    try:
        tree = ast.parse(path.read_bytes())
    except (OSError, SyntaxError, ValueError):
        # ast.parse on bytes honours PEP 263 coding cookies; a file that still cannot be
        # parsed (bad encoding, null byte, a read error) is REPORTED, not skipped.
        return None
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".", 1)[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".", 1)[0])
    return tuple(sorted(roots))


class AstArchDepsCensus:
    """The default implementation: an AST census of ``repo/ddflow/**/*.py``."""

    def scan(self, repo: Path) -> Census:
        pkg = repo / "ddflow"
        if not pkg.is_dir():
            return Census(())
        stdlib = frozenset(sys.stdlib_module_names)  # stdlib_module_names: 3.10+
        member_of = {m: name for name, members in STDLIB_FAMILIES.items() for m in members}
        stdlib_importers: dict[str, set[str]] = {name: set() for name in STDLIB_FAMILIES}
        third_party: dict[str, set[str]] = {}
        unparsed: list[str] = []

        for path in sorted(p for p in pkg.rglob("*.py") if "__pycache__" not in p.parts):
            module = _dotted(repo, path)
            roots = _external_roots(path)
            if roots is None:
                unparsed.append(module)
                continue
            for root in roots:
                if root in member_of:
                    stdlib_importers[member_of[root]].add(module)
                elif root != "ddflow" and root not in stdlib:
                    third_party.setdefault(root, set()).add(module)

        reports: list[FamilyReport] = [
            FamilyReport(
                name, STDLIB_FAMILIES[name], STDLIB_HOMES.get(name, ()), tuple(sorted(mods))
            )
            for name, mods in stdlib_importers.items()
        ]
        reports.extend(
            FamilyReport(
                root,
                (root,),
                THIRD_PARTY_HOMES.get(root, ()),
                tuple(sorted(mods)),
                third_party=True,
            )
            for root, mods in sorted(third_party.items())
        )
        return Census(tuple(reports), tuple(sorted(unparsed)))


def scan(repo: Path) -> Census:
    """The default census of ``repo``: the port with its one default implementation."""
    return AstArchDepsCensus().scan(repo)
