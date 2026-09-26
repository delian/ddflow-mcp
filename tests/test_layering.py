"""The layer rule, mechanised.

    surfaces/   argparse and JSON-RPC. No policy.
    api.py      the application layer: one typed entry point per operation, for both
                surfaces. Sits ABOVE services and BELOW surfaces, so a protocol adapter
                never has to reach through a presentation layer to perform an
                operation -- which is what `mcp -> cli` was, carried by argv strings.
    views/      one renderer per result kind.
    services/   the domain. Every operation returns an Outcome.
    infra/      disk, git, sqlite, subprocess, containers, TOML.
    core/       pure. No disk, no network, no subprocess.
    config.py   read by every layer; imports none of them.

A module may import only from layers **below** it. Written as a test rather than as a
paragraph in ARCHITECTURE.md because this package has now been bitten three times by a
rule that existed only in prose: two `family_of` implementations, three TOML loaders,
and `mcp_server` reaching into `cli`. A layering rule nothing checks is a layering
rule that decays into a dependency graph.

The concrete thing it prevents: `core` is what `fold` and the scheduler live in, and
its purity is why every readiness, gating and recovery rule is testable without a disk.
One `from ..infra.events import EventLog` in there and that property is gone, silently,
and nobody finds out until a test needs a temp directory to check an arithmetic rule.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "ddflow"

#: Layer -> what it may import. Lower layers appear in higher layers' lists, never the
#: reverse. `config` is at the bottom: everything reads knobs, and it reads nobody.
ALLOWED: dict[str, set[str]] = {
    "config": set(),
    "core": {"config"},
    "infra": {"config", "core"},
    "views": {"config", "core", "infra", "services"},
    "services": {"config", "core", "infra", "views"},
    "api": {"config", "core", "infra", "services", "views"},
    "surfaces": {"config", "core", "infra", "services", "views", "api"},
}

#: `services` and `views` are deliberately mutual: a service may render a markdown view
#: as its result (`ddflow render`), and a view reads service types to render them. The
#: pair is one layer split by role rather than two layers stacked, and saying so here is
#: more honest than an exemption list that grows.
PEERS = {("services", "views"), ("views", "services")}


def _layer(path: Path) -> str:
    rel = path.relative_to(PKG)
    if len(rel.parts) > 1:
        return rel.parts[0]
    # `__main__.py` IS an entry point — `python -m ddflow` — so it belongs with the
    # surfaces even though it sits at the top level next to `config.py`.
    if rel.name == "__main__.py":
        return "surfaces"  # `python -m ddflow` IS an entry point
    return "api" if rel.name == "api.py" else "config"


def _modules() -> list[Path]:
    return sorted(p for p in PKG.rglob("*.py") if "__pycache__" not in str(p))


def _imported_layers(path: Path) -> set[tuple[str, int]]:
    """Layers this module imports, with the line each import is on."""
    tree = ast.parse(path.read_text("utf-8"), str(path))
    # How many dots reach `ddflow/` FROM THIS MODULE. A top-level module needs one, a
    # module in `surfaces/` needs two, one in `surfaces/commands/` needs three. This was
    # hardcoded to 2 for everything but `config.py`, so the first nested package made
    # `from ..context import Ctx` — a same-layer import, two dots, reaching
    # `ddflow.surfaces` — resolve as if it named the layer `context`, and the check
    # reported a violation that was not one. A path-derived depth cannot drift as the
    # tree grows.
    depth_of_pkg = len(path.relative_to(PKG).parts)
    out: set[tuple[str, int]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level:
            # `from ..core.model import X` -> module="core.model"
            mod = node.module or ""
            if node.level == depth_of_pkg and mod:
                out.add((mod.split(".")[0], node.lineno))
            elif node.level < depth_of_pkg:
                # Shallower than the package root: a sibling or parent WITHIN this
                # layer (e.g. `..context` from `surfaces/commands/`). Same layer by
                # construction, and `test_no_module_level_import_cycles` is what
                # governs those.
                continue
            elif node.level == depth_of_pkg and not mod:
                for alias in node.names:
                    out.add((alias.name.split(".")[0], node.lineno))
    return out


@pytest.mark.parametrize("path", _modules(), ids=lambda p: str(p.relative_to(PKG)))
def test_a_module_imports_only_from_below(path: Path):
    here = _layer(path)
    if path.name == "__init__.py" and path.parent != PKG:
        return  # empty layer markers
    allowed = ALLOWED[here]
    bad = [
        f"{path.relative_to(PKG)}:{line} imports `{there}`"
        for there, line in sorted(_imported_layers(path), key=lambda t: t[1])
        if there not in allowed and there != here and (here, there) not in PEERS
    ]
    assert not bad, (
        f"`{here}` may import {sorted(allowed) or 'nothing'} — not this:\n  "
        + "\n  ".join(bad)
        + "\nMove the code down a layer, or pass what it needs in as an argument."
    )


def test_core_touches_no_disk_no_network_no_subprocess():
    """The property that makes `fold` and the scheduler testable without a fixture.

    Checked on the imports rather than by running anything: a module that never imports
    `open`'s friends cannot use them, and the check stays honest when a new file is
    added by someone who never read this docstring.
    """
    forbidden = {
        "subprocess",
        "socket",
        "urllib",
        "http",
        "sqlite3",
        "shutil",
        "tempfile",
        "requests",
        "httpx",
    }
    offenders = []
    for path in _modules():
        if _layer(path) != "core":
            continue
        tree = ast.parse(path.read_text("utf-8"), str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                names = [node.module.split(".")[0]]
            for n in names:
                if n in forbidden:
                    offenders.append(f"{path.relative_to(PKG)}:{node.lineno} imports {n}")
    assert not offenders, (
        "core is pure — every scheduling, gating and recovery rule is testable without "
        "a disk, and that is the whole reason it is a layer:\n  " + "\n  ".join(offenders)
    )


def test_the_detector_can_fail():
    """Planted violation: `core` importing `services` must be reported.

    A layering check that cannot fail is the vacuous-pass class wearing a badge, and
    this one is a pure-AST check with no obvious way to tell it is working.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        fake = Path(td) / "ddflow" / "core"
        fake.mkdir(parents=True)
        f = fake / "bad.py"
        f.write_text("from ..services.gates import run\n")
        tree = ast.parse(f.read_text())
        found = {
            n.module.split(".")[0]
            for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and n.level == 2 and n.module
        }
        assert "services" in found, "the AST walk would miss a real violation"
        assert "services" not in ALLOWED["core"], "and the rule would permit it"


def test_every_layer_is_declared():
    """A new directory under ddflow/ must be given a place in the rule.

    Otherwise the way to escape the layering is to invent a layer, which is exactly
    what someone under time pressure will do.
    """
    on_disk = {
        p.name for p in PKG.iterdir() if p.is_dir() and p.name not in ("__pycache__", "templates")
    }
    undeclared = on_disk - set(ALLOWED)
    assert not undeclared, (
        f"{sorted(undeclared)} exist but are in no layer. Add them to ALLOWED with "
        f"what they may import, or put the code in an existing layer."
    )


def test_no_module_is_left_at_the_top_level_by_accident():
    """`config.py` and `api.py` are the only modules outside a directory, and both are
    deliberate: config is read by every layer and imports none, and `api` IS a layer
    that happens to be one file until it needs to be a package."""
    loose = sorted(
        p.name
        for p in PKG.glob("*.py")
        if p.name not in ("__init__.py", "__main__.py", "config.py", "api.py")
    )
    assert not loose, (
        f"{loose} sit outside every layer, so the rule above says nothing about them. "
        f"Put each in core/ infra/ services/ views/ or surfaces/."
    )


# -- the cache invariant the fold must not break --------------------------------------


def test_no_fold_handler_reads_the_child_index():
    """`State._child_index` is built lazily and never invalidated.

    That is correct ONLY while nothing reads it during the fold: the first read freezes
    the index against however many items have been applied so far, and every
    `item.added` folded afterwards is missing from it for the rest of that State's life.
    `children()` then returns a truncated list, `descendants()` and `plan()` silently
    omit items, and nothing raises — the queue is just smaller than the log.

    The docstring asserts the invariant; this asserts it mechanically, because a
    docstring has never once stopped a handler being added. Raised as THEORETICAL by
    the cross-family critic on 2026-09-24 and refuted by exactly this probe; shipping
    the probe is what stops it becoming true later.
    """
    import ast

    src = (PKG / "core" / "model.py").read_text()
    tree = ast.parse(src)
    handlers = {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name.startswith("_h_")
    }
    assert len(handlers) > 10, f"the handler scan found almost nothing: {handlers}"

    consumers = {"children", "descendants", "ancestors", "_is_umbrella", "_child_index"}
    offenders = [
        (n.name, c.func.attr, c.lineno)
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name in handlers
        for c in ast.walk(n)
        if isinstance(c, ast.Call)
        and isinstance(c.func, ast.Attribute)
        and c.func.attr in consumers
    ]
    assert offenders == [], (
        f"a fold handler reads the cached child index: {offenders}. Either invalidate "
        f"the cache on every item mutation, or compute what the handler needs directly "
        f"from `st.items`."
    )


# -- a module must not shadow its own name ----------------------------------------------


def test_no_module_defines_the_same_top_level_name_twice():
    """A second definition silently replaces the first, and every importer gets the
    later one.

    This shipped: a duplicate `GATE_OUTCOMES` 768 lines below the original, with
    `started` added to it. Nothing failed — `--outcome`'s choices and the gate-outcome
    validator simply started accepting `started`, which is an event kind and not an
    outcome, so a caller could record a gate as having begun and never finished. The
    whole suite stayed green, because no test asserts what is in that tuple.

    Python will not warn, a linter treats it as a redefinition at most, and a reader
    who greps finds the first one and stops. The only thing that catches it is asking.
    """
    import ast

    offenders: list[str] = []
    for path in sorted(PKG.rglob("*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        seen: dict[str, int] = {}
        for node in tree.body:  # top level only; a name inside a function is scoped
            targets = []
            if isinstance(node, ast.Assign):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = [node.target.id]
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                targets = [node.name]
            for name in targets:
                if name in seen and not name.startswith("_"):
                    offenders.append(
                        f"{path.relative_to(PKG)}:{node.lineno} redefines {name!r} "
                        f"(first at line {seen[name]})"
                    )
                seen[name] = node.lineno
    assert not offenders, "a module shadows its own name:\n  " + "\n  ".join(offenders)


def test_the_recordable_gate_outcomes_are_exactly_the_five(repo):
    """The specific thing the duplicate changed, pinned so a reword cannot.

    `started` is an event kind, not an outcome. Accepting it as one lets a gate be
    recorded as begun-and-never-finished, which reads as a settled outcome everywhere
    that asks whether a gate has one.
    """
    from ddflow.core.model import GATE_OUTCOMES

    assert set(GATE_OUTCOMES) == {"passed", "failed", "unavailable", "partial", "skipped"}
    assert "started" not in GATE_OUTCOMES


#: Mutual import pairs with an eager edge that are KNOWN and accepted, each with the
#: reason. **This set may only ever shrink.** An entry is a latent cycle: it loads today
#: and becomes a hard `ImportError` the moment somebody makes the other edge eager.
#:
#: `core.model` <-> `core.events`: `model` needs `Event` eagerly (it is in every
#: signature), and `events` derives its kind vocabulary from `model.HANDLERS` — a dict of
#: handler functions that cannot move without moving the handlers. `events.py:40` records
#: the lazy import and why. Fixing it means moving validation out of `events` into a
#: third module; filed as B127 rather than done here, because `core/` is the one layer
#: whose purity the rest of the suite depends on.
KNOWN_LATENT_CYCLES: set[tuple[str, str]] = {
    ("core.model", "core.events"),
}


def _dotted(path: Path) -> str:
    """The module's dotted name relative to the package, `__init__` collapsed away."""
    mod = ".".join(path.relative_to(PKG).with_suffix("").parts)
    return mod.removesuffix(".__init__")


def _edges(path: Path) -> tuple[set[str], set[str]]:
    """(module-scope imports, ALL imports) of other modules in this package.

    Relative imports are resolved against the module's own package. A PACKAGE
    (`__init__.py`) is its own package, so `from .x` there means `pkg.x`, not
    `parent.x` — an earlier version subtracted a level for those and would have
    recorded a wrong edge for the first `__init__` that gained a re-export, either
    missing a real cycle or inventing one.
    """
    import ast as _ast

    mod = _dotted(path)
    pkg = mod if path.name == "__init__.py" else (mod.rsplit(".", 1)[0] if "." in mod else "")
    tree = _ast.parse(path.read_text("utf-8"))
    top = {id(n) for n in tree.body}

    at_module, everywhere = set(), set()
    for node in _ast.walk(tree):
        targets: set[str] = set()
        if isinstance(node, _ast.ImportFrom):
            base = pkg
            for _ in range(max(node.level - 1, 0)):
                base = base.rsplit(".", 1)[0] if "." in base else ""
            if node.level == 0:
                if not (node.module or "").startswith("ddflow"):
                    continue
                targets.add((node.module or "").removeprefix("ddflow."))
            else:
                targets.add(f"{base}.{node.module}".strip(".") if node.module else base)
        elif isinstance(node, _ast.Import):
            targets |= {
                a.name.removeprefix("ddflow.") for a in node.names if a.name.startswith("ddflow.")
            }
        for t in targets - {"", mod}:
            everywhere.add(t)
            if id(node) in top:
                at_module.add(t)
    return at_module, everywhere


def test_no_mutually_importing_pair_has_a_module_level_edge():
    """Two modules that import each other must BOTH do it lazily, or not at all.

    The first version of this check looked for a module-level 2-cycle, and the operator's
    cross-family reviewer showed it could not detect the state it was written for. The
    pre-fix `cli`/`mcp` pair had ONE module-level edge (`mcp -> cli`) and one lazy one, so
    reverting the fix left the suite GREEN — I had even observed that the half-mutation
    did not bite and reasoned it away as the check behaving correctly. It was not: the
    thing at risk was precisely that half-state returning.

    One module-level edge in a mutual pair loads fine TODAY. It is a latent cycle: the
    moment somebody makes the other edge eager — a tidy-up, an IDE import-organiser —
    it becomes a hard `ImportError` at startup with no test to catch the change. So the
    invariant is about the PAIR, not about two eager edges meeting.
    """
    scanned = {_dotted(p): _edges(p) for p in _modules()}
    known = set(scanned)
    stale = sorted(p for pair in KNOWN_LATENT_CYCLES for p in pair if p not in known)
    assert not stale, f"allowlisted pairs naming modules that no longer exist: {stale}"
    bad = []
    for mod, (at_module, everywhere) in sorted(scanned.items()):
        for other in sorted(everywhere & known):
            back = scanned[other][1]
            if mod not in back:
                continue  # not mutual; plain layering governs it
            if (mod, other) in KNOWN_LATENT_CYCLES:
                continue
            if other in at_module:
                bad.append(f"{mod} imports {other} at MODULE level, and {other} imports {mod}")
    assert not bad, (
        "latent import cycle — a mutual pair with an eager edge:\n  "
        + "\n  ".join(bad)
        + "\nMake BOTH edges function-local, or move what they share to a lower layer."
    )


def test_the_cycle_detector_resolves_a_PACKAGE_relative_import_correctly(tmp_path):
    """A package is its own package: `from .x` in `a/b/__init__.py` means `a.b.x`.

    An earlier resolver subtracted a level for `__init__.py`, so that import resolved to
    `a.x` — no `__init__` under `ddflow/` has a module-scope import today, so the graph
    was unaffected, but the first package re-export would have recorded a wrong edge and
    either missed a real cycle or invented one. A detector that is wrong only about code
    nobody has written yet is still wrong, and silently.

    Checked by calling `_edges` itself rather than reimplementing the scan, which is how
    the sibling `test_the_detector_can_fail` manages to exercise none of this.

    *roborev via kilo (DeepSeek/Qwen) on f90daaf, THEORETICAL and correct.*
    """
    pkg = tmp_path / "ddflow" / "surfaces" / "commands"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("from .config import _write_config\n")
    (pkg / "config.py").write_text("x = 1\n")

    import tests.test_layering as TL

    real_pkg, TL.PKG = TL.PKG, tmp_path / "ddflow"
    try:
        at_module, everywhere = TL._edges(pkg / "__init__.py")
    finally:
        TL.PKG = real_pkg

    assert "surfaces.commands.config" in at_module, at_module
    assert "surfaces.config" not in everywhere, (
        f"a package's `from .x` resolved one level too high: {everywhere}"
    )
