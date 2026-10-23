"""Conformance (D-RA-1, operator 2026-10-23): no operator-only refusal is a dead end.

Every refusal that tells the caller to use a terminal must also name the chat route (an
`--approval` flag or `ddflow approvals ...`), or be listed in `sites.TERMINAL_ONLY` with the
reason it is terminal-only (T3). A user who cannot reach the host's shell must still be able to
act through the agent's chat wherever a route exists."""

from __future__ import annotations

import ast
from pathlib import Path

from ddflow.services.approvals import sites as S

ROOT = Path(__file__).resolve().parents[1]
PHRASES = ("own terminal", "from a terminal", "at a terminal", "at their terminal")
ROUTE = ("--approval", "ddflow approvals", "approvals ask", "approvals attest")
SKIP_DIRS = ("ddflow/config_sections/", "ddflow/templates/")


def _scopes(tree: ast.AST):
    """(qualified function name or <module>, node) for the module and every function."""
    yield "<module>", tree

    def walk(node: ast.AST, prefix: str):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                name = f"{prefix}.{child.name}" if prefix else child.name
                yield name, child
                yield from walk(child, name)
            else:
                yield from walk(child, prefix)

    yield from walk(tree, "")


def _strings(scope: ast.AST) -> list[str]:
    """The non-docstring string constants of ``scope`` (a nested function's included)."""
    docs = set()
    for n in ast.walk(scope):
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Module):
            if (
                n.body
                and isinstance(n.body[0], ast.Expr)
                and isinstance(getattr(n.body[0], "value", None), ast.Constant)
            ):
                docs.add(id(n.body[0].value))
    return [
        n.value
        for n in ast.walk(scope)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs
    ]


def refusals(base: Path = ROOT) -> dict[str, list[str]]:
    """key "module:function" -> the strings that name a terminal; for innermost functions only."""
    found: dict[str, list[str]] = {}
    for path in sorted((base / "ddflow").rglob("*.py")):
        rel = path.relative_to(base).as_posix()
        if rel.startswith(SKIP_DIRS):
            continue
        tree = ast.parse(path.read_text())
        for name, scope in _scopes(tree):
            own = _own_strings(scope)
            hits = [t for t in own if any(p in t for p in PHRASES)]
            if hits:
                found[f"{rel}:{name}"] = _strings(scope)
    return found


def _own_strings(scope: ast.AST) -> list[str]:
    """Strings of ``scope`` outside its nested functions (so a phrase belongs to one scope)."""
    out: list[str] = []

    def walk(node: ast.AST):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                out.append(child.value)
            walk(child)

    walk(scope)
    body = getattr(scope, "body", [])
    first = body[0] if body else None
    if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant):
        doc = first.value.value
        out = [t for t in out if t is not doc]  # a docstring explains, it does not refuse
    return out


def test_no_terminal_only_refusal_is_a_dead_end():
    dead = []
    for key, strings in refusals().items():
        if key in S.TERMINAL_ONLY or key in S.NOT_A_REFUSAL or key in S.NOT_YET_ROUTED:
            continue
        if not any(r in t for t in strings for r in ROUTE):
            dead.append(key)
    assert not dead, (
        "these refusals send the caller to a terminal with no chat route and are not listed "
        f"in sites.TERMINAL_ONLY with a reason: {dead}"
    )


def test_every_listed_exemption_is_real_and_has_a_reason():
    found = refusals()
    for key, why in S.NOT_YET_ROUTED.items():
        assert "RA-consume-sites-" in why, f"{key}: name the task that routes it"
    for table in (S.TERMINAL_ONLY, S.NOT_A_REFUSAL, S.NOT_YET_ROUTED):
        for key, why in table.items():
            assert key in found, f"{key} no longer names a terminal: drop it from the table"
            assert len(why) > 20, key
    for key in S.TERMINAL_ONLY:
        assert not any(r in t for t in found[key] for r in ROUTE), (
            f"{key} now offers a route: it is not terminal-only any more"
        )


def test_the_detector_finds_a_dead_end(tmp_path):
    pkg = tmp_path / "ddflow"
    pkg.mkdir()
    (pkg / "a.py").write_text(
        'def refuse():\n    raise ValueError("run it from your own terminal")\n'
        'def routed():\n    raise ValueError("own terminal, or --approval request")\n'
        'def documented():\n    """Explains: own terminal."""\n    return 1\n'
    )
    found = refusals(tmp_path)
    assert set(found) == {"ddflow/a.py:refuse", "ddflow/a.py:routed"}
    assert not any(r in t for t in found["ddflow/a.py:refuse"] for r in ROUTE)
    assert any(r in t for t in found["ddflow/a.py:routed"] for r in ROUTE)
