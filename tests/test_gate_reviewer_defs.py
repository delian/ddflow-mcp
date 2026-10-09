"""One definition each of "a reviewer gate" and "a required gate" (B-uni-gate-record.2).

Reviewer gates come from `GateDef.reviewer` (`reviewer_gates`); required gates from
`required_gates`, which `[gates].required` decides and every `GateDef.required` follows.
"""

from __future__ import annotations

import dataclasses

from ddflow.core import progress as PR
from ddflow.services import gates as G
from ddflow.services.gates.defs import DEFAULT_GATES


def test_required_gates_is_the_knob_and_the_definitions_follow_it(repo, cfg):
    gates = G.load_gates(repo, cfg)
    assert G.required_gates(cfg) == tuple(cfg.gates.required), "knob order, not hash order"
    assert G.required_gates(cfg, gates) == G.required_gates(cfg), "loaded definitions agree"
    assert {g for g, d in gates.items() if d.required} == set(cfg.gates.required)


def test_a_gate_taken_out_of_the_knob_is_no_longer_required_by_its_default(repo, cfg):
    """`implement` is built in as required; a project that drops it from
    `[gates].required` has dropped it, in the definition as in every enforcement point."""
    assert DEFAULT_GATES["implement"].required
    cfg2 = dataclasses.replace(
        cfg, gates=dataclasses.replace(cfg.gates, required=["unit_tests", "merge"])
    )
    gates = G.load_gates(repo, cfg2)
    assert not gates["implement"].required
    assert "implement" not in G.required_gates(cfg2, gates)
    assert "implement" not in G.required_gates(cfg2)


def test_a_definition_flagged_required_is_required(cfg):
    flagged = {"extra": dataclasses.replace(DEFAULT_GATES["research"], id="extra", required=True)}
    assert "extra" in G.required_gates(cfg, flagged)
    assert "extra" not in G.required_gates(cfg)


def test_the_progress_review_gates_are_declared_reviewer_gates():
    """core/progress cannot read definitions; its constant must stay inside them."""
    assert PR.REVIEW_GATES <= set(G.reviewer_gates(None))


def test_a_gate_table_required_flag_never_reaches_the_definition(repo, cfg, capsys):
    """`[gate.<id>] required` is popped by the loader (B4d206ede45): it never was
    enforced, so deriving the flag from the knob drops nothing that worked before."""
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "gates.toml").write_text("[gate.extra]\nrequired = true\n", "utf-8")
    cfg2 = dataclasses.replace(cfg, gates=dataclasses.replace(cfg.gates, required=[]))
    gates = G.load_gates(repo, cfg2)
    assert not gates["extra"].required and "extra" not in G.required_gates(cfg2, gates)
    assert "does not read" in capsys.readouterr().err
