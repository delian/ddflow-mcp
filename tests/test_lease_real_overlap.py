"""Lease overlap is judged on the files a holder actually changed, not on glob prefixes.

`globs_overlap` is prefix-based by design: `a/**` overlaps `a/x.py` though the holder never
touches `x.py`. `core.admission.real_overlap` is the one predicate (shared with
`services.leases`' claim/update path) that re-judges those pairs against the holder's
actual changed files, so disjoint files under one directory run in parallel; it falls back
to the prefix rule only when the holder has no worktree (changed paths unknown).
"""

from __future__ import annotations

from ddflow.core.admission import conflicts, real_overlap


def test_a_wildcard_glob_that_never_touched_my_file_does_not_overlap():
    # Mine is under the same directory prefix, but the holder never changed x.py.
    assert real_overlap(["a/x.py"], ["a/**"], changed=["a/y.py"]) == []


def test_a_wildcard_glob_over_a_file_the_holder_did_change_overlaps():
    assert real_overlap(["a/y.py"], ["a/**"], changed=["a/y.py"]) == [("a/y.py", "a/**")]


def test_two_wildcard_globs_overlapping_only_on_untouched_files_do_not_overlap():
    assert real_overlap(["a/**"], ["a/**"], changed=["a/y.py"]) == [("a/**", "a/**")]


def test_an_exact_declared_path_is_work_changed_yet_or_not():
    # An exact (wildcard-free) lease path is declared work: it overlaps even when the
    # holder's changed files are empty (it has not written yet).
    assert real_overlap(["src/a.py"], ["src/a.py"], changed=[]) == [("src/a.py", "src/a.py")]


def test_unknown_changed_paths_fall_back_to_the_prefix_rule():
    # No worktree -> git cannot say: unknown is never "disjoint", so prefix overlap stands.
    assert real_overlap(["a/x.py"], ["a/**"], changed=None) == [("a/x.py", "a/**")]


def test_conflicts_stays_prefix_based():
    # The pure prefix predicate is unchanged; the real judgment is a separate, explicit call.
    assert conflicts(["a/x.py"], ["a/**"]) == [("a/x.py", "a/**")]


def test_a_changed_file_inside_a_shared_glob_does_not_make_an_overlap():
    # A shared (regenerated/pinned) path is exempt: changing it holds nothing.
    assert real_overlap(["a/x.py"], ["a/**"], changed=["a/gen.md"], shared=["a/gen.md"]) == []


def test_a_changed_file_outside_the_holders_glob_does_not_block():
    # mine `src/**` vs theirs `src/pkg/**`: the holder only changed `src/other.py`, which
    # is NOT under its `src/pkg/**` glob, so the two are disjoint and run in parallel.
    assert real_overlap(["src/**"], ["src/pkg/**"], changed=["src/other.py"]) == []


def test_a_changed_file_under_both_globs_blocks():
    # The holder changed `src/pkg/a.py`, under both its glob and the claimant's: a real
    # collision on that file.
    assert real_overlap(["src/**"], ["src/pkg/**"], changed=["src/pkg/a.py"]) == [
        ("src/**", "src/pkg/**")
    ]
