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
        "max_hops": g.unit_tests_max_hops,
        "name_max_tests": g.unit_tests_name_max_tests,
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


@pytest.fixture
def spelled(repo):
    """pkg.lookup defines one function; `test_reads_by_name` spells it without importing it."""
    files = {
        "pkg/__init__.py": "",
        "pkg/lookup.py": "def other_helper_name():\n    return 1\n",
        "tests/conftest.py": "",
        "tests/test_reads_by_name.py": (
            "def test_r():\n    assert 'compute_unique_lookup_key' in 'compute_unique_lookup_key'\n"
        ),
        "tests/test_unrelated.py": "def test_u():\n    assert True\n",
    }
    _write(repo, files)
    return repo


def test_a_test_that_spells_a_name_the_change_defines_is_selected(spelled):
    (spelled / "pkg/lookup.py").write_text(
        "def other_helper_name():\n    return 1\n\n\ndef compute_unique_lookup_key():\n    return 2\n"
    )
    sel = T.select(spelled, "main")
    assert sel is not None
    got = {t.path: t.reason for t in sel.tests}
    assert got == {
        "tests/test_reads_by_name.py": "names compute_unique_lookup_key, changed in pkg/lookup.py"
    }, got


def test_a_name_spelt_by_too_many_tests_selects_none_of_them(spelled):
    for i in range(3):
        (spelled / f"tests/test_also_{i}.py").write_text("X = 'compute_unique_lookup_key'\n")
    _git(spelled, "add", "-A")
    _git(spelled, "commit", "-qm", "more spellers")
    (spelled / "pkg/lookup.py").write_text("def compute_unique_lookup_key():\n    return 2\n")
    sel = T.select(spelled, "main", name_max_tests=3)
    assert sel is not None and sel.tests == [], "four tests spell it: a word, not a name"
    sel = T.select(spelled, "main", name_max_tests=4)
    assert sel is not None and len(sel.tests) == 4


@pytest.fixture
def listed(repo):
    """pkg.zoo and a test that LISTS it (quoted name), one that imports it, one that mentions it
    in a comment."""
    files = {
        "pkg/__init__.py": "",
        "pkg/zoo/__init__.py": "",
        "pkg/zoo/old.py": "OLD = 1\n",
        "tests/conftest.py": "",
        "tests/test_layout_of_zoo.py": (
            "import importlib, pkgutil\nPACKAGE = 'pkg.zoo'\n\n"
            "def test_each_area():\n"
            "    for m in pkgutil.iter_modules(importlib.import_module(PACKAGE).__path__):\n"
            "        assert m.name\n"
        ),
        "tests/test_uses_zoo.py": "from pkg.zoo import old\n\ndef test_u():\n    assert old.OLD\n",
        "tests/test_comment.py": "# the pkg.zoo package\n\ndef test_c():\n    assert True\n",
    }
    _write(repo, files)
    return repo


def test_a_module_added_to_a_package_selects_the_test_that_lists_the_package(listed):
    (listed / "pkg/zoo/brand_new.py").write_text("NEW = 1\n")
    sel = T.select(listed, "main")
    assert sel is not None
    got = {t.path: t.reason for t in sel.tests}
    assert got == {
        "tests/test_layout_of_zoo.py": "lists package pkg.zoo (module pkg/zoo/brand_new.py added or removed)"
    }, got


def test_a_module_removed_from_a_package_selects_it_too_and_an_edit_does_not(listed):
    (listed / "pkg/zoo/old.py").write_text("OLD = 2\n")
    sel = T.select(listed, "main")
    assert sel is not None
    assert "tests/test_layout_of_zoo.py" not in {t.path for t in sel.tests}, (
        "an edit lists nothing new"
    )
    (listed / "pkg/zoo/old.py").unlink()
    sel = T.select(listed, "main")
    assert sel is not None
    assert "tests/test_layout_of_zoo.py" in {t.path for t in sel.tests}


def test_a_committed_new_module_counts_as_added(listed):
    _git(listed, "checkout", "-qb", "work")
    (listed / "pkg/zoo/brand_new.py").write_text("NEW = 1\n")
    _git(listed, "add", "-A")
    _git(listed, "commit", "-qm", "add a module")
    sel = T.select(listed, "main")
    assert sel is not None
    assert "tests/test_layout_of_zoo.py" in {t.path for t in sel.tests}


def test_a_new_module_in_this_repositorys_split_packages_selects_the_split_guard():
    """B0e7199ff32: `services/gates/regression_collect.py` was added and `ddflow tests --item`
    left out tests/test_module_splits.py, which failed only in the full ci run."""
    root = Path(__file__).resolve().parents[1]
    for new in (
        "ddflow/services/gates/brand_new_area.py",
        "ddflow/api/knowledge/brand_new_area.py",
        "ddflow/api/reporting/brand_new_area.py",
    ):
        sel = T.select(root, "main", changed=[new], membership=[new], idents={})
        assert sel is not None
        assert "tests/test_module_splits.py" in {t.path for t in sel.tests}, new


def test_a_renamed_module_counts_as_a_removal_and_an_addition(listed):
    """Git's rename detection must not hide the change of what the package lists."""
    _git(listed, "checkout", "-qb", "work")
    _git(listed, "mv", "pkg/zoo/old.py", "pkg/zoo/renamed.py")
    _git(listed, "commit", "-qm", "rename a module")
    sel = T.select(listed, "main")
    assert sel is not None
    assert "tests/test_layout_of_zoo.py" in {t.path for t in sel.tests}
