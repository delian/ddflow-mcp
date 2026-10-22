"""RA-inventory: the gated-command table is complete (a refusal site cannot appear unlisted).

The scan is on the AST, so whitespace, strings, comments and docstrings cannot hide or fake a
call, and it COUNTS calls per module: a second call in an already-listed module changes the
count and fails until the table is amended (`MARKER_CALLS`). Sites with no marker call
(relayed consent, hook guards) are held by an anchor: the text they hang on must still exist.
"""

from __future__ import annotations

import ast
from pathlib import Path

from ddflow.services.approvals import sites as S

ROOT = Path(__file__).resolve().parents[1]


def _called_name(node: ast.Call) -> str:
    f = node.func
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""


def _scan(tree: ast.AST, rel: str, found: dict[str, int], aliased: list[str]) -> None:
    def walk(node: ast.AST, fn: str) -> None:
        for child in ast.iter_child_nodes(node):
            where = child.name if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) else fn
            if isinstance(child, ast.Call) and _called_name(child) in S.MARKER_NAMES:
                key = f"{rel}:{fn}"
                found[key] = found.get(key, 0) + 1
            if isinstance(child, ast.ImportFrom):
                aliased.extend(
                    f"{rel}: {a.name} as {a.asname}"
                    for a in child.names
                    if a.name in S.MARKER_NAMES and a.asname and a.asname != a.name
                )
            walk(child, where)

    walk(tree, "<module>")


def _calls(base: Path = ROOT) -> tuple[dict[str, int], list[str]]:
    found: dict[str, int] = {}
    aliased: list[str] = []
    for path in sorted((base / "ddflow").rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        _scan(tree, path.relative_to(base).as_posix(), found, aliased)
    return found, aliased


def _outside_infrastructure(found: dict[str, int]) -> dict[str, int]:
    return {k: n for k, n in found.items() if k.split(":")[0] not in S.INFRASTRUCTURE}


def test_the_marker_call_sites_are_exactly_the_ones_the_table_declares():
    found, aliased = _calls()
    assert not aliased, f"a marker imported under another name hides its calls: {aliased}"
    assert _outside_infrastructure(found) == S.MARKER_CALLS, (
        "a call to agent_marker/is_agent was added, moved or removed: give the verb it gates a "
        "Site row in ddflow/services/approvals/sites.py (tier, minimum level, what is "
        "digested), name the function in the row's `functions`, and update MARKER_CALLS"
    )


def test_every_row_function_is_counted_and_every_counted_function_has_a_row():
    named = {f for s in S.SITES for f in s.functions}
    assert named == set(S.MARKER_CALLS)
    for m in S.INFRASTRUCTURE:
        assert (ROOT / m).is_file(), m


def test_a_new_refusal_site_would_be_caught(tmp_path):
    pkg = tmp_path / "ddflow" / "services"
    pkg.mkdir(parents=True)
    # whitespace before the paren, a '#' inside a string before the call, a second call
    (pkg / "newgate.py").write_text(
        "def f(a):\n"
        "    if why := agent_marker (a):\n"
        "        return why\n"
        '    label = "#"; return agent_marker(a) or label\n'
    )
    # a definition, a docstring and a comment mention it but never call it
    (pkg / "quiet.py").write_text(
        'def agent_marker(x):\n    """Use agent_marker(x) here."""\n    return x  # agent_marker(x)\n'
    )
    (pkg / "alias.py").write_text("from ddflow.services.identity import agent_marker as _am\n")
    found, aliased = _calls(tmp_path)
    assert found == {"ddflow/services/newgate.py:f": 2}
    assert aliased == ["ddflow/services/alias.py: agent_marker as _am"]


def test_a_call_moved_to_another_function_is_caught(tmp_path):
    """The verb-level binding: the same module, the same count, a different function."""
    pkg = tmp_path / "ddflow" / "services"
    pkg.mkdir(parents=True)
    (pkg / "approval.py").write_text("def other(a):\n    return agent_marker(a)\n")
    found, _ = _calls(tmp_path)
    assert found != {"ddflow/services/approval.py:grant": 1}


def test_sites_without_a_marker_call_are_anchored():
    for s in S.SITES:
        if s.functions:
            assert s.anchor == ("", ""), s.verb
        else:
            path, text = s.anchor
            assert path and text, f"{s.verb} has no marker call and no anchor"
            assert text in (ROOT / path).read_text(), f"{s.verb}: {text!r} is gone from {path}"


def test_rows_are_well_formed_and_the_amended_acts_are_listed():
    verbs = [s.verb for s in S.SITES]
    assert len(verbs) == len(set(verbs))
    for s in S.SITES:
        assert s.tier in S.TIERS and s.family in S.FAMILIES, s.verb
        assert s.min_level in (S.L1, S.L2, S.L3, S.L4), s.verb
        assert s.digest and s.command, s.verb
        # D-RA-Q6: T3 is signed only, T2 never needs more than a person channel
        assert (s.tier == S.T3) == (s.min_level == S.L4), s.verb
    for required in ("gate.skip.failed_review", "grant.add", "claim.force", "complete.force"):
        assert S.site(required).tier == S.T2
