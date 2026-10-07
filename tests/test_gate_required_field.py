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


def test_it_is_refused_in_ddflows_own_tree(repo, cfg, monkeypatch):
    _write(repo, TABLE)
    monkeypatch.setattr(D, "_is_code_tree", lambda root: True)
    with pytest.raises(ValueError, match=r"gates\]\.required"):
        G.load_gates(repo, cfg)


def test_the_real_knob_is_untouched(repo, cfg, capsys):
    _write(repo, '[gate.security_review]\ntitle = "Security review"\n')
    cfg.gates.required = [*cfg.gates.required, "security_review"]
    gates = G.load_gates(repo, cfg)
    assert gates["security_review"].required and capsys.readouterr().err == ""
