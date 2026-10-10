"""The mutations this repository registers for its own gates (`ddflow gate verify`).

A registered mutation whose `old` text drifted away is reported by `verify` as a failure
only when somebody runs it, which costs a whole suite. Checking here that each one still
applies to exactly one place keeps the proof that a gate can go red from rotting
silently (mutation pass 2026-10-10)."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _registered() -> list[tuple[str, dict[str, str]]]:
    gates = tomllib.loads((ROOT / ".ddflow" / "config.toml").read_text("utf-8")).get("gate", {})
    return [(gid, m) for gid, spec in gates.items() for m in spec.get("mutations", [])]


def test_unit_tests_has_a_registered_mutation() -> None:
    assert any(gid == "unit_tests" for gid, _ in _registered())


def test_ci_has_a_registered_mutation() -> None:
    assert any(gid == "ci" for gid, _ in _registered())


def test_every_registered_mutation_still_applies_exactly_once() -> None:
    for gid, mut in _registered():
        src = (ROOT / mut["file"]).read_text("utf-8")
        # `gate verify` runs this suite with the mutation APPLIED, so the file then holds
        # `new` and not `old`: accept either, or this test would turn the unit_tests gate
        # red for ANY mutation and the verification would prove nothing.
        applied = src.count(mut["new"]) == 1 and mut["old"] not in src
        assert src.count(mut["old"]) == 1 or applied, f"{gid}: {mut['file']} lost its `old`"
        assert mut["old"] != mut["new"], f"{gid}: the mutation changes nothing"
