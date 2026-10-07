"""B16: the tests a change reaches are derived from the diff, and run in parallel.

"Choosing which tests to run by reasoning about the change is guessing — derive it."
Each rule below is a statement about a real git repository and a real import graph,
because the selection is only worth anything if it names the tests a change can break
and leaves out the ones it cannot.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.services import gates as G
from ddflow.services import testselect as T

OK, FAIL, NOTHING = 0, 1, 2


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def proj(repo):
    """pkg.a <- pkg.b <- pkg.c (a chain), pkg.api re-exporting c, and a test per level."""
    files = {
        "pkg/__init__.py": "",
        "pkg/a.py": "def one():\n    return 1\n",
        "pkg/b.py": "from . import a\n\ndef two():\n    return a.one() + 1\n",
        "pkg/c.py": "from pkg import b\n\ndef three():\n    return b.two() + 1\n",
        "pkg/widget.py": "WIDTH = 3\n",
        "pkg/api/__init__.py": "from pkg.c import three\n",
        "tests/conftest.py": "",
        "tests/test_a.py": "from pkg.a import one\n\ndef test_one():\n    assert one() == 1\n",
        "tests/test_b.py": "import pkg.b\n\ndef test_two():\n    assert pkg.b.two() == 2\n",
        "tests/test_c.py": "from pkg.c import three\n\ndef test_three():\n    assert three() == 3\n",
        "tests/test_facade.py": "from pkg.api import three\n\ndef test_f():\n    assert three()\n",
        "tests/sub/test_deep.py": "def test_deep():\n    assert True\n",
        "tests/test_widget_misc.py": "def test_w():\n    assert True\n",
    }
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "fixture")
    return repo


def _picked(repo: Path) -> dict[str, str]:
    sel = T.select(repo, "main")
    assert sel is not None
    return {t.path: t.reason for t in sel.tests}


def test_a_leaf_change_reaches_its_importers_within_two_hops(proj):
    (proj / "pkg/a.py").write_text("def one():\n    return 1  # changed\n")
    got = _picked(proj)
    assert got["tests/test_a.py"] == "imports pkg.a"
    assert got["tests/test_b.py"] == "imports pkg.b, which imports pkg.a", (
        "a relative import (`from . import a`) must resolve to pkg.a"
    )
    assert "tests/test_c.py" not in got, "three hops away is layering, not evidence"
    assert "tests/test_facade.py" not in got, "the graph does not continue through a facade"


def test_the_graph_does_not_continue_through_a_package_facade(proj):
    """`pkg/api/__init__.py` imports pkg.c, and test_facade imports pkg.api: two hops, but
    importing a re-exporting facade is not evidence a test exercises pkg.c."""
    (proj / "pkg/c.py").write_text("from pkg import b\n\ndef three():\n    return 3\n")
    got = _picked(proj)
    assert got == {"tests/test_c.py": "imports pkg.c"}, got


def test_a_new_test_file_is_selected_before_it_is_committed(proj):
    (proj / "tests/test_new.py").write_text("def test_n():\n    assert True\n")
    assert _picked(proj) == {"tests/test_new.py": "changed"}


def test_a_changed_conftest_selects_every_test_beneath_it(proj):
    (proj / "tests/conftest.py").write_text("import os\n")
    got = _picked(proj)
    assert "tests/sub/test_deep.py" in got and "tests/test_c.py" in got
    assert got["tests/test_a.py"] == "under changed tests/conftest.py"


def test_a_file_nothing_imports_is_matched_by_name(proj):
    (proj / "pkg/widget.py").write_text("WIDTH = 4\n")
    assert _picked(proj) == {"tests/test_widget_misc.py": "named after widget"}


def test_committed_branch_work_counts_as_well_as_the_working_tree(proj):
    _git(proj, "checkout", "-qb", "feature")
    (proj / "pkg/a.py").write_text("def one():\n    return 1  # committed\n")
    _git(proj, "commit", "-qam", "change a")
    assert "tests/test_a.py" in _picked(proj)


@pytest.mark.parametrize(
    ("configured", "want"),
    [
        (
            "uv run pytest tests/ -q --timeout=60 -n 4",
            "uv run pytest -q --timeout=60 -n 4 tests/test_a.py",
        ),
        ("pytest tests/", "pytest -n auto tests/test_a.py"),  # xdist declared: parallel
        ("pytest tests/ -p no:xdist", "pytest -p no:xdist tests/test_a.py"),  # deliberate
    ],
)
def test_the_run_command_keeps_the_projects_runner_and_runs_in_parallel(tmp_path, configured, want):
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[dependency-groups]\ndev = ["pytest", "pytest-xdist"]\n'
    )
    assert T.run_command(configured, ["tests/test_a.py"], tmp_path) == want


def test_a_project_that_does_not_run_pytest_gets_the_files_not_a_guess(tmp_path):
    assert T.run_command("npm test", ["src/a.test.js"], tmp_path) == ""
    assert T.run_command("", ["tests/test_a.py"], tmp_path) == ""


def test_ddflow_tests_lists_why_and_the_command(proj):
    run_cli(proj, "init")
    run_cli(proj, "config", "--set", "gate.unit_tests.command", "pytest tests/ -q -n 2")
    (proj / "pkg/a.py").write_text("def one():\n    return 1  # changed\n")
    code, out, err = run_cli(proj, "tests")
    assert code == OK, err
    assert "tests/test_a.py  -- imports pkg.a" in out
    assert "pytest -q -n 2 tests/test_a.py tests/test_b.py" in out
    assert "The unit_tests gate still runs the whole suite" in out
    code, out, _ = run_cli(proj, "--json", "tests")
    body = json.loads(out)
    assert {t["path"] for t in body["tests"]} == {"tests/test_a.py", "tests/test_b.py"}


def test_a_change_no_test_reaches_is_exit_2_and_not_a_pass(proj):
    run_cli(proj, "init")
    (proj / "README.md").write_text("# proj, edited\n")
    code, out, _ = run_cli(proj, "tests")
    assert code == NOTHING
    assert "not a pass" in out and "whole suite" in out


def test_doctor_names_a_test_command_that_uses_one_core(repo):
    run_cli(repo, "init")
    (repo / "pyproject.toml").write_text('[dependency-groups]\ndev = ["pytest", "pytest-xdist"]\n')
    run_cli(repo, "config", "--set", "gate.unit_tests.command", "pytest -q")
    _code, out, _ = run_cli(repo, "doctor")
    assert "runs pytest on ONE core although pytest-xdist is declared" in out, out


def test_the_agent_is_told_to_run_relevant_tests_in_parallel_and_all_at_the_gate():
    """The guidance is the deliverable as much as the command: an agent never told to run
    `ddflow tests` keeps guessing, and one told only that skips the full suite."""
    driver = (
        Path(__file__).resolve().parents[1] / "ddflow/templates/drivers/implement-phase.md"
    ).read_text()
    assert "ddflow tests --item <ID>" in driver
    assert "never record unit_tests from a run of\n  your own" in driver
    assert "it runs the **whole** suite on the branch merged with\n  the base" in driver
    prompt = G.DEFAULT_GATES["unit_tests"].prompt
    assert "WHOLE suite" in prompt and "-n auto" in prompt and "ddflow tests" in prompt
    assert "ci runs the WHOLE suite on the merge result and passes" in prompt


# -- regressions from the rubber-duck review of B16 ----------------------------------


def test_an_options_value_is_kept_even_when_it_names_a_file(tmp_path):
    """`-c pytest.ini` lost its value: every existing path was stripped as if it were a
    test location, so the test file became the config file."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    got = T.run_command("pytest -c pytest.ini tests/ -q", ["tests/test_a.py"], tmp_path)
    assert got == "pytest -c pytest.ini -q tests/test_a.py"
    got = T.run_command("pytest tests/test_old.py::test_x -q", ["tests/test_a.py"], tmp_path)
    assert got == "pytest -q tests/test_a.py", "a node id is a test location too"


def test_the_mcp_tool_diffs_the_checkout_the_agent_is_standing_in(proj, tmp_path):
    """Without `item`, MCP diffed the PRIMARY checkout: a live change in the agent's
    worktree read as 'nothing changed'."""
    from ddflow.surfaces.mcp import Server

    tree = tmp_path / "agent-tree"
    _git(proj, "worktree", "add", "-q", "-b", "agent", str(tree))
    (tree / "pkg/a.py").write_text("def one():\n    return 1  # in the worktree\n")
    reply = Server(proj, called_from=tree / "pkg").handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_tests", "arguments": {}},
        }
    )
    body = json.loads(reply["result"]["content"][0]["text"])
    assert Path(body["tree"]).resolve() == tree.resolve()
    assert {t["path"] for t in body["tests"]} == {"tests/test_a.py", "tests/test_b.py"}


def test_importers_of_a_deleted_module_are_selected(proj):
    """A deleted module's importers are now BROKEN -- the tests most worth running -- and
    were missed because only surviving modules seeded the graph."""
    _git(proj, "rm", "-q", "pkg/a.py")
    got = _picked(proj)
    assert got.get("tests/test_a.py") == "imports pkg.a"
    assert got.get("tests/test_b.py") == "imports pkg.b, which imports pkg.a"


def test_importers_of_a_renamed_module_are_selected(proj):
    """`git diff --name-only` reports a rename as its NEW path only; the old module's
    importers still name the old one."""
    _git(proj, "mv", "pkg/a.py", "pkg/renamed.py")
    got = _picked(proj)
    assert "tests/test_a.py" in got and "tests/test_b.py" in got


def test_a_name_match_is_a_whole_word_not_a_substring(tmp_path, repo):
    for path in ("pkg/cli.py", "tests/test_client.py", "tests/test_cli_parity.py"):
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text("X = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "names")
    (repo / "pkg/cli.py").write_text("X = 2\n")
    assert _picked(repo) == {"tests/test_cli_parity.py": "named after cli"}


@pytest.fixture
def data(proj):
    """Data files the tests read by path, never by import: a guard baseline whose name the
    test builds at run time, a fixture tree, and a note outside any test directory."""
    files = {
        "tests/ratchet_counts/widgets.toml": "baseline = 2\n",
        "tests/fixtures/vendored-spec/COPYING": "MIT\n",
        "tests/fixtures/vendored-spec/v1/spec.json": "{}\n",
        "docs/notes.md": "notes\n",
        # reads COUNTS / f"{kind}.toml": only the directory is ever spelt out
        "tests/test_guard.py": 'COUNTS = "ratchet_counts"\n\ndef test_g():\n    assert 1\n',
        "tests/test_conformance.py": (
            'SCHEMAS = ("fixtures/vendored-spec", "spec.json", "COPYING")\n\n'
            "def test_s():\n    assert 1\n"
        ),
        # writes a COPYING of its own: the bare name is not evidence it reads this one
        "tests/test_licence_writer.py": 'NAME = "COPYING"\n\ndef test_l():\n    assert 1\n',
        "tests/test_prose.py": 'DOC = "notes.md"\n\ndef test_p():\n    assert 1\n',
    }
    for path, text in files.items():
        (proj / path).parent.mkdir(parents=True, exist_ok=True)
        (proj / path).write_text(text)
    _git(proj, "add", "-A")
    _git(proj, "commit", "-qm", "data files")
    return proj


def test_a_changed_baseline_selects_the_test_that_reads_its_directory(data):
    """B20b7744905: lowering one guard baseline (a data-only change) selected nothing, so
    the guard test that reads it never ran in the fast loop."""
    (data / "tests/ratchet_counts/widgets.toml").write_text("baseline = 1\n")
    assert _picked(data) == {
        "tests/test_guard.py": "names data file tests/ratchet_counts/widgets.toml"
    }


def test_a_fixture_is_matched_by_its_name_with_its_directory_before_its_bare_name(data):
    (data / "tests/fixtures/vendored-spec/COPYING").write_text("Apache-2.0\n")
    (data / "tests/fixtures/vendored-spec/v1/spec.json").write_text('{"v": 1}\n')
    assert _picked(data) == {
        "tests/test_conformance.py": "names data file tests/fixtures/vendored-spec/COPYING"
    }


def test_a_data_file_outside_every_test_directory_selects_nothing_new(data):
    (data / "docs/notes.md").write_text("changed\n")
    assert _picked(data) == {}


def test_a_changed_conftest_is_not_also_read_as_a_data_file(data):
    """A conftest.py has its own rule (every test beneath it); a test that merely mentions
    the name `conftest.py` does not read the one in tests/sub."""
    (data / "tests/sub/conftest.py").write_text("import os\n")
    (data / "tests/test_mentions.py").write_text('C = "conftest.py"\n')
    _git(data, "add", "tests/test_mentions.py")
    _git(data, "commit", "-qm", "mentions")
    assert _picked(data) == {"tests/sub/test_deep.py": "under changed tests/sub/conftest.py"}


def test_an_unreadable_test_names_nothing_and_does_not_fail_the_selection(data):
    locked = data / "tests/test_locked.py"
    locked.write_text('COUNTS = "ratchet_counts"\n')
    _git(data, "add", "tests/test_locked.py")
    _git(data, "commit", "-qm", "locked")
    locked.chmod(0)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("this user reads a mode-000 file (root): nothing to show")
        (data / "tests/ratchet_counts/widgets.toml").write_text("baseline = 1\n")
        # git cannot compare what it cannot read, so it lists test_locked as changed
        assert _picked(data) == {
            "tests/test_guard.py": "names data file tests/ratchet_counts/widgets.toml",
            "tests/test_locked.py": "changed",
        }
    finally:
        locked.chmod(0o644)


def test_a_farther_directory_alone_is_not_evidence(data):
    """Rereview of B20b7744905: only the NEAREST directory stands in for a file name the
    test builds at run time; `fixtures` is in every test that reads any fixture."""
    (data / "tests/fixtures/deep/report.json").parent.mkdir(parents=True)
    (data / "tests/fixtures/deep/report.json").write_text("{}\n")
    (data / "tests/test_other_fixture.py").write_text('F = "fixtures/elsewhere.json"\n')
    _git(data, "add", "-A")
    _git(data, "commit", "-qm", "more data")
    (data / "tests/fixtures/deep/report.json").write_text('{"x": 1}\n')
    assert _picked(data) == {}, "nothing names report.json or `deep`"


def test_a_file_named_without_its_directory_is_still_read(data):
    """`FIXTURES / "lines.ndjson"` with FIXTURES from a conftest: the test spells the
    name and no directory, and still reads the file."""
    (data / "tests/fixtures/sets/lines.ndjson").parent.mkdir(parents=True)
    (data / "tests/fixtures/sets/lines.ndjson").write_text("{}\n")
    (data / "tests/test_run_time_path.py").write_text('C = FIXTURES / "lines.ndjson"\n')
    _git(data, "add", "-A")
    _git(data, "commit", "-qm", "corpus")
    (data / "tests/fixtures/sets/lines.ndjson").write_text('{"x": 1}\n')
    assert _picked(data) == {
        "tests/test_run_time_path.py": "names data file tests/fixtures/sets/lines.ndjson"
    }


def test_without_name_and_directory_every_test_spelling_the_name_is_taken_by_choice(data):
    """The cost of the fallback, pinned so it is a choice and not an accident: with no
    test spelling name and directory, a test that only writes a file of the same name is
    taken too. Text cannot tell it from the reader; one extra test is cheaper than a
    missed one."""
    (data / "tests/fixtures/sets/lines.ndjson").parent.mkdir(parents=True)
    (data / "tests/fixtures/sets/lines.ndjson").write_text("{}\n")
    (data / "tests/test_run_time_path.py").write_text('C = FIXTURES / "lines.ndjson"\n')
    (data / "tests/test_writes_own.py").write_text('(tmp_path / "lines.ndjson").write_text("")\n')
    _git(data, "add", "-A")
    _git(data, "commit", "-qm", "corpus")
    (data / "tests/fixtures/sets/lines.ndjson").write_text('{"x": 1}\n')
    why = "names data file tests/fixtures/sets/lines.ndjson"
    assert _picked(data) == {"tests/test_run_time_path.py": why, "tests/test_writes_own.py": why}


def test_a_noisy_nearest_directory_does_not_hide_the_reader_that_spells_the_name(data):
    """Rereview of B20b7744905: `fixtures` is in every test that reads any fixture; a
    reader spelling only `FIXTURES / "lines.ndjson"` must still be taken."""
    (data / "tests/fixtures/lines.ndjson").write_text("{}\n")
    (data / "tests/test_run_time_path.py").write_text('C = FIXTURES / "lines.ndjson"\n')
    (data / "tests/test_other_fixture.py").write_text('F = "fixtures/elsewhere.json"\n')
    _git(data, "add", "-A")
    _git(data, "commit", "-qm", "lines")
    (data / "tests/fixtures/lines.ndjson").write_text('{"x": 1}\n')
    got = _picked(data)
    assert got["tests/test_run_time_path.py"] == "names data file tests/fixtures/lines.ndjson"
    assert "tests/test_other_fixture.py" in got, "the nearest directory is taken as well"


def test_a_data_file_directly_in_a_test_directory_is_matched_by_its_name(data):
    (data / "tests/expected.json").write_text("{}\n")
    (data / "tests/test_beside.py").write_text('E = HERE / "expected.json"\n')
    _git(data, "add", "-A")
    _git(data, "commit", "-qm", "expected")
    (data / "tests/expected.json").write_text('{"x": 1}\n')
    assert _picked(data) == {"tests/test_beside.py": "names data file tests/expected.json"}


@pytest.mark.parametrize(
    ("text", "word", "named"),
    [
        ('"fixtures/vendored-spec/spec.json"', "spec.json", True),
        ('"old_spec.json"', "spec.json", False),
        ('"spec.json.orig"', "spec.json", False),
        ('tmp_path / "COPYING.txt"', "COPYING", False),
        ("# the counts live in ratchet_counts.", "ratchet_counts", True),
        ('ROOT / "ratchet_counts_old"', "ratchet_counts", False),
    ],
)
def test_a_name_is_matched_whole_not_inside_a_longer_name(text, word, named):
    assert T._names(text, word) is named
