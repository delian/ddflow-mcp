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
from ..services import archifaces as I


def archdeps(
    repo: Path,
    *,
    agent: str = "",
    interfaces: str = "",
    baseline: bool = False,
) -> O.Outcome:
    """The census. ``families`` is one row per dependency family (family, members, home,
    importers, violations, third_party); ``text`` is the human table. Read-only: a pure
    AST scan of ``repo/ddflow``, it never folds the event log."""
    if interfaces or baseline:
        return _interfaces(repo, "" if interfaces in ("", "all") else interfaces, baseline)
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


def _interfaces(repo: Path, kind: str, baseline: bool) -> O.Outcome:
    """The interface census (ADP-census-v2): programs run, wire protocols, other tools' files,
    environment variables and network endpoints, with call sites (file:line), planned home
    task and violations. ``kind`` narrows to one kind (CLI/MCP ``interfaces=KIND``); ``baseline`` returns the
    ratchet's baseline text (what `tests/arch_baselines/arch_interfaces.txt` holds) instead.
    When that file is committed the view also reports the ratchet (``ratchet.grew`` /
    ``ratchet.shrank``) against it."""
    if kind and kind not in I.KINDS:
        return O.failed(
            "archdeps",
            f"unknown kind {kind!r}: one of {', '.join(I.KINDS)}; "
            "call ddflow_view action=archdeps interfaces=all",
        )
    census = I.scan(repo)
    if baseline and census.unparsed:
        return O.failed(
            "archdeps",
            f"cannot write a baseline: {len(census.unparsed)} file(s) do not parse "
            f"({', '.join(census.unparsed[:3])}); fix them, then call ddflow archdeps "
            "--interfaces all --baseline",
        )
    if baseline:
        return O.ok("archdeps", text=I.format_baseline(census), unparsed=list(census.unparsed))
    fams = [f for f in census.families if not kind or f.kind == kind]
    rows = [
        {
            "kind": f.kind,
            "family": f.family,
            "home": f.home,
            "home_modules": list(f.home_modules),
            "sites": [{"path": s.path, "line": s.line, "detail": s.detail} for s in f.sites],
            "violations": len(f.violations),
        }
        for f in fams
    ]
    text = render_interfaces(fams, census.unparsed)
    data: dict[str, object] = {"interfaces": rows, "unparsed": list(census.unparsed)}
    committed = repo / I.BASELINE_PATH
    if committed.is_file():
        grew, shrank = I.ratchet(census, I.parse_baseline(committed.read_text()))
        if kind:  # the ratchet shown matches the kind shown
            grew, shrank = (
                tuple(x for x in grew if x.startswith(f"{kind} ")),
                tuple(x for x in shrank if x.startswith(f"{kind} ")),
            )
        data["ratchet"] = {"grew": list(grew), "shrank": list(shrank), "baseline": I.BASELINE_PATH}
        text += f"\nratchet vs {I.BASELINE_PATH}: {len(grew)} new or grown, {len(shrank)} to lower"
        text += "".join(f"\n  {line}" for line in (*grew, *shrank))
    return O.ok("archdeps", text=text, **data)


def render_interfaces(fams: list[I.InterfaceFamily], unparsed: tuple[str, ...]) -> str:
    """One readable summary: a per-kind total, then one row per family (sites in the JSON)."""
    lines = []
    for k in I.KINDS:
        mine = [f for f in fams if f.kind == k]
        if mine:
            sites = sum(len(f.sites) for f in mine)
            lines.append(f"{k}: {len(mine)} families, {sites} call sites")
    lines.append("kind          family                    home                 sites  violations")
    for f in fams:
        lines.append(
            f"{f.kind:<13} {f.family:<25} {f.home:<20} {len(f.sites):>5} {len(f.violations):>11}"
        )
    lines.extend(f"unparsed       {m}" for m in unparsed)
    lines.append("call sites: ddflow archdeps --interfaces <kind> --json")
    return "\n".join(lines)
