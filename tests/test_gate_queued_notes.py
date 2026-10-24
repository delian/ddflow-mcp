"""B46339c052b: the gate-status why-note assembly lives in the gates package it describes,
and the metrics package never reaches into gates (the two-way dependency _why_notes once
sat on: gates.outcomes -> metrics.queued -> gates.reasons)."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

PKG = Path(__file__).resolve().parents[1] / "ddflow"


def test_metrics_never_imports_gates():
    """`metrics.queued` imported `gates.reasons` while `gates.outcomes` imported metrics
    back: two packages that reach into each other. One eager edge and startup breaks."""
    offenders = []
    for f in sorted((PKG / "services" / "metrics").rglob("*.py")):
        tree = ast.parse(f.read_text("utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.level
                and (node.module or "").split(".")[0] == "gates"
            ):
                offenders.append(f"{f.relative_to(PKG)}:{node.lineno}")
    assert not offenders, f"metrics never imports gates: {offenders}"


def test_the_note_assembly_lives_in_gates():
    from ddflow.services.gates.status_notes import _queued_notes, _why_notes

    assert callable(_queued_notes) and callable(_why_notes)
