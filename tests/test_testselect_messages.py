"""Bb5b8b17161: a reworded message and a bare-imported helper reach the tests that rely on them.

``claim_text`` reworded a CLI message (f94fd1f1); ``test_asagent_tree.py`` asserts on it, and
reached the change only through the data model (a hub) and the api facade, so the precise
selection dropped it and the recall replay failed on main. Two routes the import graph misses:

* a test that holds the TEXT a source line gained or lost (a run of words from a string
  literal), however far its imports are from that file;
* a test that says ``from _wait import x`` imports ``tests/_wait.py``, a module of a directory
  pytest puts on ``sys.path`` and not of a package.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from helpers import git_quiet as _git

from ddflow.services import testidents as TI
from ddflow.services import testreach as TR
from ddflow.services import testselect as T

USERS = 12
OLD = '    return f"not adopted: the tree you are in is {owner}\'s working tree"\n'
NEW = '    return f"leased to {agent}: the tree you are in is {owner}\'s, so it was not adopted"\n'


def _write(repo: Path, files: dict[str, str]) -> None:
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "fixture")


@pytest.fixture
def messaged(repo):
    """pkg.cli.claim_text <- pkg.hub <- {USERS tests}; test_message imports only the hub and
    asserts on the message's text."""
    files = {
        "pkg/__init__.py": "",
        "pkg/cli.py": "def claim_text(owner):\n" + OLD,
        "pkg/hub.py": "from pkg import cli\n\nTEXT = cli.claim_text\n",
        "tests/conftest.py": "",
        "tests/test_message.py": (
            "from pkg import hub\n\ndef test_m():\n"
            "    assert 'the tree you are in is' in str(hub.TEXT)\n"
        ),
    }
    for i in range(USERS):
        files[f"tests/test_user_{i}.py"] = (
            f"from pkg import hub\n\ndef test_u{i}():\n    assert hub\n"
        )
    _write(repo, files)
    return repo


def test_a_test_that_holds_the_text_a_message_lost_is_selected_through_a_hub(messaged):
    (messaged / "pkg/cli.py").write_text("def claim_text(owner):\n" + NEW)
    sel = T.select(messaged, "main", hub_importers=USERS)
    assert sel is not None and sel.full == ""
    got = {t.path: t.reason for t in sel.tests}
    assert set(got) == {"tests/test_message.py"}, got
    assert "the tree you are" in got["tests/test_message.py"], got


def test_a_phrase_that_many_tests_say_is_not_a_message(messaged):
    _write(
        messaged,
        {
            f"tests/test_user_{i}.py": (messaged / f"tests/test_user_{i}.py").read_text()
            + "# the tree you are in\n"
            for i in range(USERS)
        },
    )
    (messaged / "pkg/cli.py").write_text("def claim_text(owner):\n" + NEW)
    sel = T.select(messaged, "main", hub_importers=USERS, name_max_tests=5)
    assert sel is not None and sel.tests == [], sel.tests


def test_phrases_are_the_words_of_the_string_literals_a_line_gains_or_loses():
    got = TI._phrases([OLD, NEW, "    x = a_function_name(1, 2, 3)  # not a literal at all\n"])
    assert "the tree you are" in got and "it was not adopted" in got
    assert not any("function" in p for p in got), got
    assert all(len(p) >= TI._PHRASE_MIN for p in got)


@pytest.fixture
def barely(repo):
    _write(
        repo,
        {
            "pkg/__init__.py": "",
            "pkg/core.py": "X = 1\n",
            "tests/conftest.py": "",
            "tests/_wait.py": "def wait_until(f):\n    return f()\n",
            "tests/test_poll.py": "from _wait import wait_until\n\ndef test_w():\n    assert wait_until(int)\n",
            "tests/test_core.py": "from pkg import core\n\ndef test_c():\n    assert core.X\n",
        },
    )
    return repo


def test_a_bare_import_of_a_tests_directory_helper_reaches_its_importers(barely):
    (barely / "tests/_wait.py").write_text("def wait_until(f):\n    return f() or 1\n")
    sel = T.select(barely, "main")
    assert sel is not None
    assert {t.path for t in sel.tests} == {"tests/test_poll.py"}, sel.tests


def test_a_bare_name_that_is_no_file_of_the_repository_resolves_to_nothing():
    homes = TR._bare_homes(["tests/_wait.py", "tests/test_a.py", "pkg/__init__.py", "pkg/m.py"])
    assert TR._bare("_wait", "tests/test_a.py", homes) == "tests._wait"
    assert TR._bare("json", "tests/test_a.py", homes) == ""
    assert TR._bare("m", "tests/test_a.py", homes) == "", "pkg is a package, not on sys.path"


def test_a_removed_line_that_begins_with_dashes_is_content_not_a_diff_header():
    diff = (
        "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n@@ -1,2 +1,2 @@\n"
        '-    sql = "-- fetch the rows that were not adopted yet"\n'
        '+    sql = "++ fetch the rows that were adopted already"\n'
    )
    got = TI._added_removed(diff)
    assert got == {
        "m.py": [
            '    sql = "-- fetch the rows that were not adopted yet"',
            '    sql = "++ fetch the rows that were adopted already"',
        ]
    }, got


def test_phrases_of_many_files_come_from_one_diff_not_three_git_calls_each(messaged, monkeypatch):
    files = {f"pkg/m{i}.py": f'MSG = "message number {i} says something long"\n' for i in range(7)}
    _write(messaged, files)
    for f in files:
        (messaged / f).write_text(f'MSG = "reworded text of the {f} message goes here"\n')
    calls = []
    real = TI.W.git
    monkeypatch.setattr(TI.W, "git", lambda *a, **k: calls.append(a) or real(*a, **k))
    got = TI.changed_phrases(messaged, "main", sorted(files))
    assert {got[p] for p in got} == set(files), got
    assert len(calls) <= 4, f"{len(calls)} git calls for {len(files)} files"


def test_a_hyphenated_command_line_flag_a_literal_names_is_a_phrase():
    got = TI._phrases(
        ['    "--add-needs": "CLI only", "--force": "x", "a--b": 1, \'--no-task\': 2\n']
    )
    assert {"--add-needs", "--no-task"} <= got and "--force" not in got and "a--b" not in got, got


def test_a_one_word_change_is_no_phrase():
    assert TI._phrases(['    f.read_text(encoding="UTF-8")\n']) == set()


def test_a_tests_directory_file_shadows_the_library_and_the_root_as_pytest_does():
    homes = TR._bare_homes(
        ["tests/queue.py", "tests/conftest.py", "conftest.py", "tests/test_a.py"]
    )
    assert TR._bare("queue", "tests/test_a.py", homes) == "tests.queue"
    assert TR._bare("conftest", "tests/test_a.py", homes) == "tests.conftest"
    assert TR._bare("queue", "other/test_b.py", homes) == "", "a directory off the import path"
    assert TR._bare("test_a", "tests/test_a.py", homes) == "tests.test_a"
    homes = TR._bare_homes(["ddflow.py", "tests/test_x.py"])
    assert TR._bare("ddflow.services.testidents", "tests/test_x.py", homes) == "", "dotted: resolve"


def test_a_root_conftest_does_not_take_the_import_a_tests_conftest_answers(repo):
    _write(
        repo,
        {
            "conftest.py": "",
            "tests/conftest.py": "def make_client():\n    return 1\n",
            "tests/test_x.py": "from conftest import make_client\n\ndef test_x():\n    assert make_client()\n",
        },
    )
    g = TR.build(repo, ["conftest.py", "tests/conftest.py", "tests/test_x.py"], [])
    assert g.importers.get("tests.conftest") == {"tests/test_x.py"}, g.importers


def test_the_diff_headers_do_not_depend_on_the_users_git_prefix_settings(messaged):
    _git(messaged, "config", "diff.mnemonicPrefix", "true")
    _write(messaged, {"pkg/m.py": 'MSG = "a message that was said before"\n'})
    (messaged / "pkg/m.py").write_text('MSG = "reworded text that holds new words"\n')
    assert "pkg/m.py" in TI.changed_phrases(messaged, "main", ["pkg/m.py"]).values()


def test_a_non_ascii_file_name_keeps_its_own_phrases(messaged):
    name = "pkg/méssage.py"
    _write(messaged, {name: 'MSG = "a message that was said before"\n', "pkg/ascii.py": "A = 1\n"})
    (messaged / name).write_text('MSG = "reworded text that holds new words"\n')
    (messaged / "pkg/ascii.py").write_text("A = 2\n")
    got = TI.changed_phrases(messaged, "main", ["pkg/ascii.py", name])
    assert set(got.values()) == {name}, got
    assert "reworded text that holds" in got


def test_a_quoted_diff_header_is_not_the_file_before_it():
    diff = (
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
        'diff --git "a/q\\"x.py" "b/q\\"x.py"\n--- "a/q\\"x.py"\n+++ "b/q\\"x.py"\n@@ -1 +1 @@\n-y = 1\n+y = 2\n'
    )
    assert TI._added_removed(diff) == {"a.py": ["x = 1", "x = 2"]}
