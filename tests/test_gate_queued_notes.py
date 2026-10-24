"""B46339c052b: the gate-status why-note assembly lives in the gates package it describes,
and the metrics package never reaches into gates (the two-way dependency _why_notes once
sat on: gates.outcomes -> metrics.queued -> gates.reasons)."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PKG = Path(__file__).resolve().parents[1] / "ddflow"
GATES = (PKG / "services" / "gates").resolve()


def _gate_imports(module_file: Path, node: ast.Import | ast.ImportFrom) -> list[str]:
    """Filesystem paths the import on ``node`` names under ddflow/services/gates."""
    if isinstance(node, ast.Import):
        targets = [(PKG.parent / name.name.replace(".", "/")) for name in node.names]
    else:
        base = module_file.parent
        for _ in range(max(node.level - 1, 0)):
            base = base.parent
        mod = node.module or ""
        if node.level == 0 and mod.startswith("ddflow."):
            base = PKG.parent
        target = (base / mod.replace(".", "/")) if mod else base
        targets = [target] + [target / name.name for name in node.names]
    svc = (PKG / "services").resolve()
    gates = GATES.resolve()
    hits = []
    for t in targets:
        r = t.resolve()
        if r == svc:
            continue  # `from ddflow import services` names the services package itself, not gates
        if r == gates or r.is_relative_to(gates):
            hits.append(str(t))
    return hits


def test_metrics_never_imports_gates():
    """`metrics.queued` imported `gates.reasons` while `gates.outcomes` imported metrics
    back: two packages that reach into each other. One eager edge and startup breaks."""
    offenders = []
    for f in sorted((PKG / "services" / "metrics").rglob("*.py")):
        tree = ast.parse(f.read_text("utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            for hit in _gate_imports(f, node):
                offenders.append(f"{f.relative_to(PKG)}:{node.lineno} ({hit.removesuffix('.py').rstrip('/')})")
    assert not offenders, f"metrics never imports gates: {offenders}"


def test_the_note_assembly_lives_in_gates():
    from ddflow.services.gates.status_notes import _queued_notes, _why_notes

    assert callable(_queued_notes) and callable(_why_notes)


def test_a_gates_submodule_named_services_is_not_excused():
    """Only the `ddflow/services` package itself is excused: an import that reaches a
    module UNDER gates keeps offending however its last path segment is spelled."""
    f = PKG / "services" / "metrics" / "x.py"
    node = ast.parse("import ddflow.services.gates.services").body[0]
    assert _gate_imports(f, node), "a gates submodule named services is still gates"
    node = ast.parse("from ddflow import services").body[0]
    assert not _gate_imports(f, node), "the services package itself is not gates"
