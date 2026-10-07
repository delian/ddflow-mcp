"""`[gate.<id>] required` is not silently accepted (bug B4d206ede45).

Every enforcement point -- gate status, `complete`, `verify`, the workflow view -- reads
`[gates].required`. `load_gates` accepted `required = true` in a gate's own table too,
and nothing read it: the gate was shown as configured and never enforced. It is now
named for what it is: refused in ddflow's own tree, warned about and skipped elsewhere.
"""

from __future__ import annotations

import pytest

from ddflow.services import gates as G
from ddflow.services.gates import defs as D

TABLE = '[gate.security_review]\ntitle = "Security review"\nrequired = true\n'


@pytest.fixture(autouse=True)
def _fresh_warnings():
    getattr(D, "_REQUIRED_WARNED", set()).clear()
    yield
    getattr(D, "_REQUIRED_WARNED", set()).clear()


def _write(repo, text: str) -> None:
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "gates.toml").write_text(text, "utf-8")


def test_a_required_flag_in_a_gate_table_is_warned_about_naming_the_real_knob(repo, cfg, capsys):
    _write(repo, TABLE)
    gates = G.load_gates(repo, cfg)
    err = capsys.readouterr().err
    assert "[gate.security_review] sets required" in err and "[gates].required" in err, err
    assert "security_review" in gates and gates["security_review"].title == "Security review"
    G.load_gates(repo, cfg)
    assert capsys.readouterr().err == "", "warned once per process, not on every load"


def test_another_root_is_still_warned_about(repo, tmp_path, cfg, capsys):
    """One long-lived server, two projects: the first warning must not hide the second."""
    _write(repo, TABLE)
    G.load_gates(repo, cfg)
    capsys.readouterr()
    other = tmp_path / "other"
    other.mkdir()
    _write(other, TABLE)
    G.load_gates(other, cfg)
    assert "[gate.security_review] sets required" in capsys.readouterr().err


def test_it_is_refused_in_ddflows_own_tree(repo, cfg, monkeypatch):
    _write(repo, TABLE)
    monkeypatch.setattr(D, "_is_code_tree", lambda root: True)
    with pytest.raises(ValueError, match=r"gates\]\.required"):
        G.load_gates(repo, cfg)


def test_false_on_a_required_gate_says_how_to_stop_requiring_it(repo, cfg, capsys):
    _write(repo, "[gate.unit_tests]\nrequired = false\n")
    assert "unit_tests" in cfg.gates.required
    assert G.load_gates(repo, cfg)["unit_tests"].required, "the knob still decides"
    assert "take 'unit_tests' out of [gates].required" in capsys.readouterr().err


def test_a_table_agreeing_with_the_knob_is_not_reported(repo, cfg, capsys, monkeypatch):
    """`required = false` on an optional gate, or `true` on a listed one, is redundant:
    no warning, and no refusal even in ddflow's own tree."""
    monkeypatch.setattr(D, "_is_code_tree", lambda root: True)
    _write(repo, "[gate.critic]\nrequired = false\n[gate.merge]\nrequired = true\n")
    G.load_gates(repo, cfg)
    assert capsys.readouterr().err == ""


def test_the_real_knob_is_untouched(repo, cfg, capsys):
    _write(repo, '[gate.security_review]\ntitle = "Security review"\n')
    cfg.gates.required = [*cfg.gates.required, "security_review"]
    gates = G.load_gates(repo, cfg)
    assert gates["security_review"].required and capsys.readouterr().err == ""
