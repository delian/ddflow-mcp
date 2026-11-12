"""Failure text is compared by a digest that ignores the ci tree's path (GM-ci-path-normalise)."""

from __future__ import annotations

import importlib

# `ddflow.services.ci.failure_text` is also the lazy name of `flakes.failure_text`, so the
# package attribute is not reliable: import the module by name.
FT = importlib.import_module("ddflow.services.ci.failure_text")

TEXT = "FAILED tests/test_a.py::test_x - AssertionError: {root}/tests/data/x.json not found"


def test_the_same_failure_from_two_tree_paths_has_one_digest() -> None:
    one = TEXT.format(root="/tmp/ddflow-ci.k3j2h1x9")
    two = TEXT.format(root="/var/tmp/ddflow-ci.Zq81_aa0")
    assert one != two
    assert FT.digest(one) == FT.digest(two)
    assert (
        FT.normalise(one)
        == "FAILED tests/test_a.py::test_x - AssertionError: <tree>/tests/data/x.json not found"
    )


def test_a_base_scratch_and_a_lane_worktree_read_the_same() -> None:
    a = TEXT.format(root="/tmp/ddflow-base.abc123")
    b = TEXT.format(root="/home/u/src/proj/.ddflow/worktrees/GM-baseline-store")
    assert FT.digest(a) == FT.digest(b)


def test_a_named_root_is_blanked_too_longest_first() -> None:
    roots = ["/srv/ci/run/7", "/srv/ci/run/7/sub"]
    one = TEXT.format(root="/srv/ci/run/7/sub")
    assert FT.normalise(one, roots).count("<tree>") == 1
    assert "/sub" not in FT.normalise(one, roots)  # not half-replaced as <tree>/sub
    two = TEXT.format(root="/srv/ci/run/9")
    assert FT.digest(one, roots) == FT.digest(two, ["/srv/ci/run/9"])


def test_a_real_new_message_still_differs() -> None:
    one = TEXT.format(root="/tmp/ddflow-ci.aaaa")
    new = one.replace("not found", "is not valid json")
    assert FT.digest(one) != FT.digest(new)


def test_a_different_repo_relative_path_still_differs() -> None:
    one = TEXT.format(root="/tmp/ddflow-ci.aaaa")
    other = one.replace("tests/data/x.json", "tests/data/y.json")
    assert FT.digest(one) != FT.digest(other)


def test_a_path_outside_any_tree_is_left_alone() -> None:
    text = "FileNotFoundError: /etc/ddflow/cfg.toml"
    assert FT.normalise(text) == text


def test_pytest_temp_dirs_are_blanked() -> None:
    one = "boom /tmp/pytest-of-ana/pytest-17/test_a0/x.txt"
    two = "boom /tmp/pytest-of-bob/pytest-3/test_a0/x.txt"
    assert FT.digest(one) == FT.digest(two)


def test_pytest_temp_blanking_keeps_what_follows_it() -> None:
    one = "boom /tmp/pytest-of-ana/pytest-17/test_a0/x.txt"
    other = "boom /tmp/pytest-of-bob/pytest-3/test_a0/y.txt"
    assert FT.normalise(one) == "boom <pytest-tmp>/test_a0/x.txt"
    assert FT.digest(one) != FT.digest(other)


def test_a_directory_above_a_pytest_temp_root_is_not_swallowed() -> None:
    one = "/repo/a/pytest-of-user/pytest-0/test.py"
    two = "/repo/b/pytest-of-user/pytest-0/test.py"
    assert FT.digest(one) != FT.digest(two)


def test_a_named_root_matches_whole_path_components_only() -> None:
    text = "/srv/ci/run/70/tests/x.json and /srv/ci/run/7-old/y and /srv/ci/run/7/z"
    got = FT.normalise(text, ["/srv/ci/run/7"])
    assert got == "/srv/ci/run/70/tests/x.json and /srv/ci/run/7-old/y and <tree>/z"
    assert FT.digest("/srv/ci/run/70/same.txt", ["/srv/ci/run/7"]) != FT.digest(
        "/other/0/same.txt", ["/other"]
    )


def test_odd_path_characters_after_a_root_or_marker_do_not_count_as_the_tree() -> None:
    assert FT.normalise("/srv/ci/run/7+extra/z", ["/srv/ci/run/7"]) == "/srv/ci/run/7+extra/z"
    assert FT.normalise("E /opt/ddflow-ci.abc-extra/f") == "E /opt/ddflow-ci.abc-extra/f"
    assert (
        FT.normalise("fail /src/pytest-of-old/pytest-0/a.py")
        == "fail /src/pytest-of-old/pytest-0/a.py"
    )
    assert FT.normalise("foo/b/c/test.py", ["/b/c"]) == "foo/b/c/test.py"


def test_roots_of_equal_length_give_one_result_in_any_order() -> None:
    text = "/a/b/c"
    assert FT.normalise(text, ["/a/b", "/b/c"]) == FT.normalise(text, ["/b/c", "/a/b"])
    assert FT.normalise(text, ["/a/b", "/b/c"]) == "<tree>/c"  # the name breaks the tie


def test_a_base_tree_and_a_worktree_root_keep_what_follows_them() -> None:
    for root in ("/tmp/ddflow-base.abc123", "/home/u/src/proj/.ddflow/worktrees/GM-x"):
        one = TEXT.format(root=root)
        other = one.replace("tests/data/x.json", "tests/data/y.json")
        new = one.replace("not found", "is not valid json")
        assert FT.digest(one) != FT.digest(other) and FT.digest(one) != FT.digest(new)


def test_roots_are_replaced_in_one_pass_and_a_sentence_end_or_bracket_closes_a_path() -> None:
    assert FT.normalise("/a/b/f", ["/a", "/b"]) == "<tree>/b/f"
    assert FT.normalise("/b/a/f", ["/a", "/b"]) == "<tree>/a/f"
    assert FT.normalise("Error: /tmp/ddflow-base.aaa111.") == "Error: <tree>."
    assert FT.normalise("see /srv/ci/run.", ["/srv/ci/run"]) == "see <tree>."
    assert FT.normalise("[/tmp/ddflow-ci.aaa/t.py]") == "[<tree>/t.py]"
    assert FT.normalise("/tmp/ddflow-ci.aaa.bak/x") == "/tmp/ddflow-ci.aaa.bak/x"


def test_a_pytest_temp_inside_a_tree_reads_the_same_from_two_trees() -> None:
    one = "File /tmp/ddflow-base.aaa/pytest-of-u/pytest-1/tests/test_a.py"
    two = "File /tmp/ddflow-ci.bbb/pytest-of-v/pytest-2/tests/test_a.py"
    assert FT.normalise(one) == "File <tree><pytest-tmp>/tests/test_a.py"
    assert FT.digest(one) == FT.digest(two)


def test_the_baseline_compare_reads_one_failure_from_two_trees_as_the_same() -> None:
    from ddflow.services.gates import baseline_red as BR

    tid = "tests/test_a.py::test_x"
    base = {tid: TEXT.format(root="/tmp/ddflow-base.abc123")}
    ci = {tid: TEXT.format(root="/tmp/ddflow-ci.def456")}
    assert BR._differs(tid, ci, base) is False  # an old red is not a new offender
    new = {tid: ci[tid].replace("not found", "is not valid json")}
    assert BR._differs(tid, new, base) is True
