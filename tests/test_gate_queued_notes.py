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
    return [
        str(t)
        for t in targets
        if t.name != "services"  # `from ddflow import services` alone names nothing in gates
        and (t.resolve() == GATES or t.resolve().is_relative_to(GATES))
    ]


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
