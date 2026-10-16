"""The Python import graph behind `testselect`: who imports a changed module, how near, and
through which hubs the walk refuses to continue (B4067086b6b).

A change reaches a test through an import. Not every import is evidence: a module that
nearly everything imports (the config loader, the data model, the event log) makes any
module IT imports "reach" nearly every test -- `config_sections/gates.py` selected 182 of
660 test files, `core/handlers/gates.py` 215, though only a handful exercise either. So an
edge is weighted by where it leads:

* a test importing the changed module DIRECTLY is evidence, always taken (hop 1);
* a test two hops away is evidence only when the module between is not a HUB (hop 2) --
  the walk is cut at a hub, and the cut is reported so nobody mistakes it for a pass;
* a changed module that IS a hub reaches everything by construction: the caller runs the
  whole suite then (`hubs_touched`), because no cheaper answer keeps recall.

A hub is a module imported by at least ``hub_importers`` distinct files of the repository
(0 = no hubs), a fact of the graph rather than a list that goes stale.
"""

from __future__ import annotations

import ast
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath


def module_name(path: str) -> str:
    """``pkg/sub/mod.py`` -> ``pkg.sub.mod``; a package's ``__init__.py`` is the package;
    a ``src/`` layout's prefix is not part of the name."""
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _statements(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Every statement under ``body``, nested ones included, expressions skipped: an import
    is a statement, and walking each expression of 1,200 files made the graph take seconds."""
    for node in body:
        yield node
        for field_ in ("body", "orelse", "finalbody"):
            sub = getattr(node, field_, None)
            if isinstance(sub, list):
                yield from _statements(sub)
        for part in (*getattr(node, "handlers", ()), *getattr(node, "cases", ())):
            yield from _statements(part.body)


def imports(path: str, source: str) -> set[str]:
    """Absolute dotted names ``path`` imports, relative imports resolved."""
    tree = ast.parse(source, filename=path)
    here = module_name(path).split(".")
    package = here if path.endswith("__init__.py") else here[:-1]
    out: set[str] = set()
    for node in _statements(tree.body):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                anchor = package[: len(package) - (node.level - 1)] if node.level > 1 else package
                target = ".".join([*anchor, *([node.module] if node.module else [])])
            else:
                target = node.module or ""
            if target:
                out.add(target)
            # `from pkg import mod` imports the MODULE when `mod` is one.
            out.update(f"{target}.{a.name}" if target else a.name for a in node.names)
    return out


def resolve(name: str, known: set[str]) -> str:
    """The longest known module that ``name`` is, or lies inside (``pkg.mod.func`` ->
    ``pkg.mod``); "" when it is not this repository's code."""
    parts = name.split(".")
    for n in range(len(parts), 0, -1):
        cand = ".".join(parts[:n])
        if cand in known:
            return cand
    return ""


@dataclass
class Graph:
    """Module -> the files importing it, over the repository's own modules."""

    importers: dict[str, set[str]] = field(default_factory=dict)
    names: set[str] = field(default_factory=set)
    unparsed: list[str] = field(default_factory=list)  #: .py files that could not be read

    def hubs(self, threshold: int) -> dict[str, int]:
        """Module -> its importer count, for each imported by ``threshold`` files or more."""
        if threshold <= 0:
            return {}
        return {m: len(fs) for m, fs in self.importers.items() if len(fs) >= threshold}


def build(tree: Path, py: list[str], changed: list[str]) -> Graph:
    """The graph of ``py`` (every .py file of the tree). A deleted (or renamed-away)
    module is still a name its importers use -- and they are now broken, the tests most
    worth running -- so it resolves like a live one."""
    known = {module_name(p): p for p in py}
    gone = {module_name(c) for c in changed if c.endswith(".py") and c not in py}
    g = Graph(names=set(known) | gone)
    for p in py:
        try:
            found = imports(p, (tree / p).read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError, ValueError):
            g.unparsed.append(p)
            continue
        for n in found:
            mod = resolve(n, g.names)
            if mod and known.get(mod) != p:
                g.importers.setdefault(mod, set()).add(p)
    return g


@dataclass
class Reach:
    """What the walk found: file -> (why, hops), and the hubs it would not walk through."""

    files: dict[str, tuple[str, int]] = field(default_factory=dict)
    cut_at: set[str] = field(default_factory=set)


def reach(g: Graph, changed: list[str], hubs: dict[str, int], max_hops: int = 2) -> Reach:
    """Every file within ``max_hops`` imports of a changed module (`[gates].unit_tests_max_hops`:
    the file imports the changed module (1), or imports a module that does (2); unbounded,
    the top layer a CLI or an MCP registry makes every test "reach" every module), breadth first so the
    reason names the nearest changed module and the path to it. The walk does not continue
    through a hub (recorded in ``cut_at``) nor through a package `__init__` re-exporting
    everything -- importing the facade is not evidence a test exercises what is behind it."""
    roots = [module_name(c) for c in changed if c.endswith(".py")]
    queue = deque((m, m, f"imports {m}", 1) for m in roots if m in g.names)
    seen: set[str] = {m for m, *_ in queue}
    out = Reach()
    while queue:
        mod, origin, why, hops = queue.popleft()
        for f in sorted(g.importers.get(mod, ())):
            fm = module_name(f)
            if f not in out.files:
                out.files[f] = (why, hops)
            if hops >= max_hops or fm in seen:
                continue
            if fm in hubs:
                out.cut_at.add(fm)
                continue
            if f.endswith("__init__.py"):
                continue
            seen.add(fm)
            queue.append((fm, origin, f"imports {fm}, which imports {origin}", hops + 1))
    return out


def hubs_touched(changed: list[str], hubs: dict[str, int]) -> list[str]:
    """The changed modules that are themselves hubs, with their importer counts."""
    return sorted(
        f"{m} ({hubs[m]} importing files)"
        for m in {module_name(c) for c in changed if c.endswith(".py")}
        if m in hubs
    )
