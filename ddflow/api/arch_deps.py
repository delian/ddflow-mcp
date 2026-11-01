"""`ddflow archdeps`: the dependency census as a CLI/JSON view.

One read-only report of every external dependency family -- third-party packages and the
stdlib families the adapter migration tracks -- with its importing modules, declared home
and violations. The Outcome carries the machine view (`families`) and the human view
(`text`); the ratchet that fails a NEW importer lives in `tests/test_arch_deps.py`.
"""

from __future__ import annotations

from pathlib import Path

from ..core import outcome as O
from ..services import archdeps as A


def archdeps(repo: Path, *, agent: str = "") -> O.Outcome:
    """The census. ``families`` is one row per dependency family (family, members, home,
    importers, violations, third_party); ``text`` is the human table. Read-only: a pure
    AST scan of ``repo/ddflow``, it never folds the event log."""
    census = A.scan(repo)
    rows = [
        {
            "family": f.family,
            "members": list(f.members),
            "home": list(f.home),
            "importers": list(f.importers),
            "violations": list(f.violations),
            "third_party": f.third_party,
        }
        for f in census.families
    ]
    return O.ok("archdeps", text=render(census), families=rows, unparsed=list(census.unparsed))


def render(census: A.Census) -> str:
    """The one human table, so the CLI and the JSON view never describe the census twice."""
    lines = [
        "family         members                home                      importers  violations"
    ]
    for f in census.families:
        lines.append(
            f"{f.family:<14} {','.join(f.members):<22} {','.join(f.home) or '-':<24} "
            f"{len(f.importers):>9} {len(f.violations):>10}"
        )
    for module in census.unparsed:
        lines.append(f"unparsed       {module}")
    return "\n".join(lines)
