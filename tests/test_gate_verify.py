"""`ddflow gate verify` — the anti-vacuous-pass check, turned on ddflow's own checks.

The pipeline has ten gates and, until this, nothing anywhere proved a single one of
them was capable of going red. A gate that cannot fail is worse than no gate: it
reports success on every change, and everyone downstream reads that as evidence. The
project's own backlog called this "the highest-value item on the list" for exactly that
reason.

Two rules make it honest, and the first is the one that is usually got wrong:

1. **A mutation that did not apply is not a passed mutation test.** If the `old` text
   is absent, or ambiguous, the check FAILS rather than skipping. A silent skip turns
   "the mutation never happened" into a green run, which reads as "the gate cannot
   detect this" — the opposite of the truth, delivered confidently.
2. **The source is restored whatever happens.** Otherwise a failed verification leaves
   a broken worktree and the next gate reports a failure that belongs to the verifier.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3

CALC = "def add(a, b):\n    return a + b\n"
TEST = "from src.calc import add\n\n\ndef test_add():\n    assert add(2, 2) == 4\n"


def _project(repo: Path, *, mutation: str = 'old = "return a + b", new = "return a - b"'):
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "calc.py").write_text(CALC)
    (repo / "test_calc.py").write_text(TEST)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "code"], check=True, capture_output=True
    )
    run_cli(repo, "init")
    (repo / ".ddflow" / "gates.toml").write_text(
        "[gate.unit_tests]\n"
        'command = "python -m pytest -q"\n'
        'cwd = "repo"\n'
        f'mutations = [\n  {{ file = "src/calc.py", {mutation} }},\n]\n'
    )
    run_cli(repo, "phase", "add", "P1", "--globs", "src/**")
    run_cli(repo, "task", "add", "T1", "--phase", "P1", "--globs", "src/calc.py")
    return repo


def test_a_gate_that_catches_its_mutation_passes(repo):
    _project(repo)
    code, out, err = run_cli(repo, "gate", "verify", "T1", "unit_tests")
    assert code == OK, f"{out}\n{err}"
    assert "CAN fail" in out, out


def test_the_source_is_restored_afterwards(repo):
    """A failed verification that leaves the tree broken makes the NEXT gate report a
    failure that is the verifier's fault — and nobody looks for that."""
    _project(repo)
    run_cli(repo, "gate", "verify", "T1", "unit_tests")
    assert (repo / "src" / "calc.py").read_text() == CALC, "the mutation was left in place"


def test_it_is_restored_even_when_the_gate_command_explodes(repo):
    _project(repo)
    (repo / ".ddflow" / "gates.toml").write_text(
        "[gate.unit_tests]\n"
        'command = "python -c \\"import sys; sys.exit(0)\\""\n'  # never fails: cannot detect
        'cwd = "repo"\n'
        'mutations = [\n  { file = "src/calc.py", old = "return a + b", new = "return a - b" },\n]\n'
    )
    run_cli(repo, "gate", "verify", "T1", "unit_tests")
    assert (repo / "src" / "calc.py").read_text() == CALC


def test_a_gate_that_cannot_see_the_mutation_fails(repo):
    """The whole point: a command that always exits 0 is a gate that proves nothing."""
    _project(repo)
    (repo / ".ddflow" / "gates.toml").write_text(
        "[gate.unit_tests]\n"
        'command = "true"\n'
        'cwd = "repo"\n'
        'mutations = [\n  { file = "src/calc.py", old = "return a + b", new = "return a - b" },\n]\n'
    )
    code, out, err = run_cli(repo, "gate", "verify", "T1", "unit_tests")
    assert code == FAIL, out
    assert "cannot see" in (out + err) or "did NOT catch" in (out + err), out + err


def test_a_mutation_that_did_not_apply_is_a_failure_not_a_skip(repo):
    """The commonest way this whole technique is satisfied on paper.

    If the text is not there, the edit never happened, the gate ran against pristine
    source and passed — and a check that treats that as success has proved the opposite
    of what it claims.
    """
    _project(repo, mutation='old = "TEXT THAT IS NOT IN THE FILE", new = "x"')
    code, out, err = run_cli(repo, "gate", "verify", "T1", "unit_tests")
    assert code == FAIL, out
    assert "0 time(s)" in (out + err), out + err
    assert "NOT a passed mutation test" in (out + err), out + err


def test_an_ambiguous_mutation_is_also_a_failure(repo):
    """Two matches means the edit lands somewhere the author did not choose."""
    _project(repo, mutation='old = "a", new = "b"')
    code, out, err = run_cli(repo, "gate", "verify", "T1", "unit_tests")
    assert code == FAIL, out
    assert "unambiguous" in (out + err), out + err


def test_a_gate_with_no_registered_mutation_is_reported_as_unproven(repo):
    """Declaring a check nobody has shown can fail is what this exists to catch."""
    _project(repo)
    (repo / ".ddflow" / "gates.toml").write_text(
        '[gate.unit_tests]\ncommand = "python -m pytest -q"\ncwd = "repo"\n'
    )
    code, out, err = run_cli(repo, "gate", "verify", "T1", "unit_tests")
    assert code == FAIL, out
    assert "NO registered mutations" in (out + err), out + err


def test_an_agent_gate_says_why_it_cannot_be_mutated(repo):
    """`bug_hunt` has no command. Reporting that plainly beats reporting a pass."""
    _project(repo)
    code, out, err = run_cli(repo, "gate", "verify", "T1", "bug_hunt")
    assert code == FAIL, out
    assert "agent gate" in (out + err), out + err
    assert "evidence contract" in (out + err), out + err


def test_the_json_surface_says_whether_it_was_verified(repo):
    _project(repo)
    _code, out, _ = run_cli(repo, "--json", "gate", "verify", "T1", "unit_tests")
    data = json.loads(out)
    assert data["verified"] is True
    assert data["results"][0]["applied"] and data["results"][0]["detected"]


def test_it_is_reachable_over_mcp(repo):
    from ddflow.surfaces.mcp import TOOLS

    assert "ddflow_gate_verify" in TOOLS, sorted(TOOLS)


def test_it_is_not_refused_by_the_pipeline_order(repo):
    """`verify` asks whether the GATE can go red, not whether the ITEM may run it.

    With `enforce_order = "block"` it was refused for any gate whose predecessors had
    not run — which is every gate, at the exact moment you most want to know whether
    the thing about to judge your work is capable of judging it.
    """
    _project(repo)
    (repo / ".ddflow" / "config.toml").write_text('[gates]\nenforce_order = "block"\n')
    code, out, err = run_cli(repo, "gate", "verify", "T1", "unit_tests")
    assert code == OK, f"exit {code} (3 = refused on pipeline order)\n{out}\n{err}"


def test_a_gate_that_is_ALREADY_red_does_not_count_as_detecting_anything(repo):
    """The anti-vacuous-pass check, being vacuous.

    A gate that exits non-zero on the UNMUTATED source — one pre-existing failing test,
    a tool that stopped being installed, a flake — reports `failed` for every mutation,
    so every `detected` is True and `verify` says "this gate CAN fail". It cannot: it
    was red before anything was touched, and the run proves nothing about its
    sensitivity to the edit. A green baseline is the precondition, and the function
    that exists to refuse unproven claims must not assume it.
    """
    _project(repo)
    (repo / "test_broken.py").write_text("def test_broken():\n    assert False\n")
    code, out, err = run_cli(repo, "gate", "verify", "T1", "unit_tests")
    assert code == FAIL, f"an already-red gate was certified as able to fail:\n{out}"
    assert "baseline" in (out + err).lower(), out + err


def test_the_baseline_failure_is_visible_in_the_json_too(repo):
    _project(repo)
    (repo / "test_broken.py").write_text("def test_broken():\n    assert False\n")
    _code, out, _ = run_cli(repo, "--json", "gate", "verify", "T1", "unit_tests")
    data = json.loads(out)
    assert data["verified"] is False
    assert "baseline" in data["reason"].lower(), data["reason"]
