"""A gate is a reviewer gate because its definition says so (bug B0e1330bf74).

`GateDef.reviewer = "different_family"` is how a gate declares that a cross-family model
reviews through it -- rubber_duck, critic and verify do. The independence check read a
hard-coded `REVIEWER_GATES` instead, so a project's own reviewer gate in gates.toml
(a security review, say) was never looked at: an item reviewed ONLY through it was
refused at completion as having no independent reviewer, and its reviewer's model was
not vetted when recorded.
"""

from __future__ import annotations

from ddflow.core.model import fold
from ddflow.services import completion as C
from ddflow.services import gates as G

CUSTOM = """
[gate.security_review]
title = "Security review"
reviewer = "different_family"
"""

DEEPSEEK = {"model": "deepseek-ai/DeepSeek-V4.1-Flash", "family": "deepseek"}


def _custom_gate_only(repo, log, cfg):
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "gates.toml").write_text(CUSTOM, "utf-8")
    gd = G.load_gates(repo, cfg)
    log.append("phase.added", "P1", {})
    log.append("task.added", "T1", {"parent": "P1"})
    G.record(log, cfg, "T1", "security_review", "passed", evidence=DEEPSEEK, gates=gd)
    return gd


def test_completion_counts_a_custom_reviewer_gate_toward_independence(repo, log, cfg):
    _custom_gate_only(repo, log, cfg)
    v = C.verdict(fold(log.read_all()), cfg, "T1", repo=repo, model="claude-opus-5-5")
    assert "deepseek" in v.independence, v.independence
    assert not any("reviewer independence" in b for b in v.blockers), v.blockers


def test_reviewer_gates_are_the_declared_ones(repo, log, cfg):
    gd = _custom_gate_only(repo, log, cfg)
    names = G.reviewer_gates(gd)
    assert {"rubber_duck", "critic", "standards", "verify", "security_review"} <= set(names)
    assert "unit_tests" not in names and "research" not in names
    # Without the definitions, the built-in set (what a caller with no repo can know).
    assert set(G.reviewer_gates(None)) == set(G.REVIEWER_GATES)


def test_a_same_family_ok_gate_is_not_a_cross_family_reviewer_gate(repo, cfg):
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "gates.toml").write_text(
        '[gate.pair_check]\nreviewer = "same_family_ok"\n', "utf-8"
    )
    assert "pair_check" not in G.reviewer_gates(G.load_gates(repo, cfg))


def test_a_definition_overrides_the_built_in_set(repo, cfg):
    """A project that lowers critic to same_family_ok has said a same-family critic is
    enough: the definition wins over the built-in list, both ways."""
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "gates.toml").write_text(
        '[gate.critic]\nreviewer = "same_family_ok"\n', "utf-8"
    )
    gd = G.load_gates(repo, cfg)
    assert not G.is_reviewer_gate("critic", gd["critic"])
    assert "critic" not in G.reviewer_gates(gd)
    assert {"rubber_duck", "standards"} <= set(G.reviewer_gates(gd))


def test_a_declared_reviewer_gate_is_one_where_its_record_is_vetted(repo, cfg):
    """`gate record` refuses the author's own model on a reviewer gate (B1979dac602) and
    checks a roborev sha's reviewer there: both ask `is_reviewer_gate`."""
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "gates.toml").write_text(CUSTOM, "utf-8")
    gd = G.load_gates(repo, cfg)
    assert G.is_reviewer_gate("security_review", gd["security_review"])
    assert not G.is_reviewer_gate("unit_tests", gd["unit_tests"])
    assert G.is_reviewer_gate("standards", gd["standards"])
