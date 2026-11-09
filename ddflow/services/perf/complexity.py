"""Growth indicators for the perf report (PM-complexity): what must not grow.

The blast radius of a small change. Pure and stdlib-only: the import graph is parsed with
``ast`` (no third-party), and the touch/hotspot/co-change indicators fold ``facts.collect``
(the leases carry each item's file globs). A figure that needs git history is ``None`` (never
zero) until a probe threads it. Every number carries its ``n``; below the sample floor it is
None. Reuses ``ddflow.core.graph`` for the depth/fan-in, never a second engine.

No prompt text, arguments, user paths or secrets appear in any figure.
"""

from __future__ import annotations

import ast
import functools
from collections import defaultdict
from pathlib import Path

from ddflow.services.metrics.facts import Facts


def import_edges(package: Path) -> dict[str, set[str]]:
    """module (dotted, package-relative) -> its direct imports, parsed by stdlib ``ast``.
    A ``.py`` file that does not parse is skipped (not an import)."""
    edges: dict[str, set[str]] = defaultdict(set)
    pkg_name = package.name
    for py in package.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        module = _module_name(package, py)
        edges[module]  # every module is a node (a leaf, a parse failure: the module STAYS)
        try:
            tree = ast.parse(py.read_text("utf-8"), filename=str(py))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue  # unreadable/parse-failed: node stays, imports unavailable (never under-counted)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    if name.startswith(pkg_name + "."):
                        name = name[len(pkg_name) + 1 :]  # package-relative, matches _module_name
                    edges[module].add(name)
                    edges[module].add(name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    target = node.module
                    if target.startswith(pkg_name + "."):
                        target = target[
                            len(pkg_name) + 1 :
                        ]  # absolute package-relative, like Import
                    edges[module].add(target)
                    edges[module].add(target.split(".")[0])
                elif (
                    node.level > 0
                ):  # a relative from .X / from . — resolve against THIS module's dir
                    dir_parts = list(py.relative_to(package).parent.parts)
                    up = node.level - 1
                    base = dir_parts[: len(dir_parts) - up] if len(dir_parts) >= up else []
                    names = [node.module] if node.module else [a.name for a in node.names]
                    for nm in names:
                        tgt = ".".join([*base, nm])
                        edges[module].add(tgt)
                        edges[module].add(tgt.split(".")[0])
    return dict(dict(edges).items())


def _module_name(package: Path, py: Path) -> str:
    rel = py.relative_to(package).with_suffix("")
    if rel.name == "__init__":
        rel = rel.parent
    return ".".join(rel.parts)


def fan_in(edges: dict[str, set[str]]) -> dict[str, int]:
    """module -> how many PACAKGE modules import it (the most-changed modules' fan-in). A
    non-package name (``os``, ``typing``, an external top-level) is not a count to surface."""
    members = set(edges)
    counts: dict[str, int] = defaultdict(int)
    for _m, deps in edges.items():
        for dep in deps & members:  # only real package nodes
            counts[dep] += 1
    return dict(counts)


def _items(facts: Facts) -> list[tuple[str, set[str]]]:
    """item -> its merged lease globs, ONE row per item (a re-claim merges, it does not
    duplicate the item across the scoreboard)."""
    per: dict[str, set[str]] = {}
    for lease in facts.leases:
        per.setdefault(lease.item, set()).update(lease.globs)
    return sorted(per.items())


def import_depth(edges: dict[str, set[str]]) -> dict[str, int]:
    """module -> its longest SIMPLE import chain out of it (dependency depth), in edges.

    Order-independent and cycle-safe: the longest path that never reuses a node, so a cycle
    contributes at most one hop and the answer does not depend on which node is entered first."""

    nodes = set(edges)

    @functools.cache
    def longest(m: str, visited: frozenset[str]) -> int:
        deps = [d for d in edges.get(m, ()) if d in nodes and d not in visited and d != m]
        return max((1 + longest(d, visited | {m}) for d in deps), default=0)

    return {m: longest(m, frozenset()) for m in nodes}


def touched(facts: Facts) -> list[dict]:
    """files and tests touched per merged item, from its merged lease globs (a re-claim merges,
    one row per item). No git needed: the lease records the item's glob breadth."""
    out = []
    for item, globs in _items(facts):
        files = list(globs)
        tests = [g for g in files if g.startswith("tests/")]
        out.append(
            {
                "item": item,
                "files": len(files),
                "tests": len(tests),
                "source_files": len(files) - len(tests),
            }
        )
    return out


def churn_hotspots(facts: Facts) -> list[dict]:
    """an item's changed lines over its merged lease globs (churn x size hotspot; one row per
    item). No outcome recorded changed lines -> ``None`` (unmeasured, never 0)."""
    out = []
    for item, globs in _items(facts):
        lines = [
            o.changed_lines
            for o in facts.outcomes
            if o.item == item and o.changed_lines is not None
        ]
        out.append({"item": item, "globs": len(globs), "changed_lines": max(lines, default=None)})
    return out


def co_change(facts: Facts) -> list[dict]:
    """two globs an item touched together (co-change pairs, one engine with OPT-history)."""
    pairs: dict[tuple[str, str], set[str]] = defaultdict(set)
    for lease in facts.leases:
        gl = sorted(set(lease.globs))
        for i, a in enumerate(gl):
            for b in gl[i + 1 :]:
                pairs[(a, b)].add(lease.item)
    return [
        {"a": a, "b": b, "n": len(items)}
        for (a, b), items in sorted(pairs.items())
        if len(items) > 1  # a co-change needs at least two items touching the same pair
    ]


def tests_per_changed_line(facts: Facts) -> dict:
    """tests per CHANGED LINE: test files the window touched (one per item), over the source
    LINES it changed (``Outcome.changed_lines``). A re-claim neither duplicates nor inflates."""
    tests = sum(1 for _item, globs in _items(facts) for g in globs if g.startswith("tests/"))
    recorded = [o.changed_lines for o in facts.outcomes if o.changed_lines is not None]
    changed = sum(recorded) if recorded else None
    return {
        "test_files": tests,
        "changed_lines": changed,
        "tests_per_line": tests / changed if changed else None,
    }


def build(facts: Facts, package: Path) -> dict:
    """The growth scoreboard: import depth/fan-in, touched, hotspots, co-change, tests-per-line."""
    edges = import_edges(package)
    return {
        "import_depth": import_depth(edges),
        "fan_in": fan_in(edges),
        "touched": touched(facts),
        "hotspots": churn_hotspots(facts),
        "co_change": co_change(facts),
        "tests_per_changed_line": tests_per_changed_line(facts),
        "not_measured": {
            "files_over_time": "the weekly files-touched series needs git history (a probe threads it)",
        },
        "next": {"cli": "ddflow metrics complexity", "mcp": "ddflow_metrics view=complexity"},
    }


__all__ = [
    "build",
    "churn_hotspots",
    "co_change",
    "fan_in",
    "import_depth",
    "import_edges",
    "tests_per_changed_line",
    "touched",
]
