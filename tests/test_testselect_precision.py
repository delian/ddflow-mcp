"""B4067086b6b: the selection stays precise when a touched module is imported, transitively,
by a hub (the config loader, the data model) that nearly every test imports.

A hub is a module that `[gates].unit_tests_hub_importers` files or more import. The walk
is cut at one, a changed hub runs the whole suite, and `[gates].unit_tests_max_files`
bounds the selection with the cut set reported (D-test-economy-r2).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git_quiet as _git

from ddflow.services import testscope as TSC
from ddflow.services import testselect as T

USERS = 12


def _write(repo: Path, files: dict[str, str]) -> None:
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "fixture")


@pytest.fixture
def hubbed(repo):
    """pkg.knob <- pkg.hub <- {USERS tests}, and pkg.leaf <- test_leaf: `hub` stands for the
    config loader (it imports its sections, and every test imports it)."""
    files = {
        "pkg/__init__.py": "",
        "pkg/knob.py": "KNOB = 1\n",
        "pkg/hub.py": "from pkg import knob\n\nVALUE = knob.KNOB\n",
        "pkg/leaf.py": "LEAF = 1\n",
        "tests/conftest.py": "",
        "tests/test_knob.py": "from pkg import knob\n\ndef test_k():\n    assert knob.KNOB\n",
        "tests/test_leaf.py": "from pkg import leaf\n\ndef test_l():\n    assert leaf.LEAF\n",
    }
    for i in range(USERS):
        files[f"tests/test_user_{i}.py"] = (
            f"from pkg import hub\n\ndef test_u{i}():\n    assert hub.VALUE\n"
        )
    _write(repo, files)
    return repo


def test_a_module_behind_a_hub_does_not_select_every_test_that_imports_the_hub(hubbed):
    (hubbed / "pkg/knob.py").write_text("KNOB = 2\n")
    sel = T.select(hubbed, "main", hub_importers=USERS)
    assert sel is not None and sel.full == ""
    got = {t.path: t.reason for t in sel.tests}
    assert got == {"tests/test_knob.py": "imports pkg.knob"}, got
    assert sel.cut_at == ["pkg.hub"], "the cut is reported, not silent"


def test_the_default_knob_values_cut_the_walk_at_a_hub_of_this_repository():
    from ddflow.config import Config

    g = Config().gates
    assert g.unit_tests_hub_importers > 0 and g.unit_tests_max_files > 0
    assert TSC.limits(Config()) == {
        "max_files": g.unit_tests_max_files,
        "hub_importers": g.unit_tests_hub_importers,
    }


def test_without_hubs_the_walk_reaches_through_them_as_before(hubbed):
    (hubbed / "pkg/knob.py").write_text("KNOB = 2\n")
    sel = T.select(hubbed, "main")
    assert sel is not None
    assert len(sel.tests) == USERS + 1


def test_a_changed_hub_is_the_whole_suite(hubbed):
    (hubbed / "pkg/hub.py").write_text("from pkg import knob\n\nVALUE = knob.KNOB + 1\n")
    sel = T.select(hubbed, "main", hub_importers=USERS)
    assert sel is not None
    assert "a hub module changed: pkg.hub" in sel.full and f"{USERS} importing files" in sel.full


def test_the_budget_cuts_the_farthest_tests_and_reports_them(hubbed):
    (hubbed / "pkg/knob.py").write_text("KNOB = 2\n")
    sel = T.select(hubbed, "main", max_files=5)
    assert sel is not None
    assert len(sel.tests) == 5 and len(sel.cut) == USERS + 1 - 5
    assert "tests/test_knob.py" in {t.path for t in sel.tests}, "the nearest test is kept"
    assert all(t.rank == T.RANK_TWO_HOP for t in sel.cut), "a direct importer outranks a far one"


def test_a_changed_test_is_never_cut(hubbed):
    (hubbed / "pkg/knob.py").write_text("KNOB = 2\n")
    for i in range(3):
        (hubbed / f"tests/test_user_{i}.py").write_text("def test_changed():\n    assert 1\n")
    sel = T.select(hubbed, "main", max_files=1)
    assert sel is not None
    assert {f"tests/test_user_{i}.py" for i in range(3)} <= {t.path for t in sel.tests}


def test_a_changed_conftest_over_the_budget_is_test_infrastructure(hubbed):
    (hubbed / "tests/conftest.py").write_text("# touched\n")
    sel = T.select(hubbed, "main", max_files=5)
    assert sel is not None
    assert "tests/conftest.py is test infrastructure" in sel.full


def test_ddflow_tests_reports_the_cut_and_the_hub(hubbed):
    run_cli(hubbed, "init")
    run_cli(hubbed, "config", "--set", "gates.unit_tests_hub_importers", str(USERS))
    run_cli(hubbed, "config", "--set", "gates.unit_tests_max_files", "1")
    (hubbed / "pkg/knob.py").write_text("KNOB = 2\n")
    code, out, err = run_cli(hubbed, "--json", "tests")
    assert code == 0, err
    body = json.loads(out)
    assert [t["path"] for t in body["tests"]] == ["tests/test_knob.py"]
    assert body["cut_at_hubs"] == ["pkg.hub"] and body["full_reason"] == ""
    _code, text, _err = run_cli(hubbed, "tests")
    assert "import walk not continued through hub(s): pkg.hub" in text


def test_a_hub_that_is_a_package_init_is_still_reported_as_the_cut(repo):
    """A facade `pkg/__init__.py` that imports the changed module and is itself a hub: the
    walk stops there and says so, rather than the cut going unreported."""
    files = {
        "pkg/__init__.py": "from pkg import m\n",
        "pkg/m.py": "M = 1\n",
        "tests/conftest.py": "",
        "tests/test_m.py": "from pkg import m\n\ndef test_m():\n    assert m.M\n",
    }
    for i in range(USERS):
        files[f"tests/test_p{i}.py"] = "import pkg\n\ndef test_p():\n    assert pkg\n"
    _write(repo, files)
    (repo / "pkg/m.py").write_text("M = 2\n")
    sel = T.select(repo, "main", hub_importers=USERS)
    assert sel is not None
    assert "pkg" in sel.cut_at
    assert {t.path for t in sel.tests} == {"tests/test_m.py"}


def test_a_removed_definition_is_attributed_to_its_neighbours_not_lost():
    from ddflow.services import testidents as TI

    diff = "@@ -3 +2,0 @@\n-def gone_function_name():\n"
    assert TI._touched_lines(diff) == {2, 3}
    assert TI._touched_lines("@@ -5,2 +5,3 @@\n") == {5, 6, 7}
