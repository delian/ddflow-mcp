"""B20: inventory ratchets, not count ratchets.

The failure being corrected, from the project ddflow was extracted from: a count-based clone
ratchet sat red for ~350 commits. Advisory, so it never blocked; a number, so every reader
learned to skip it; **24 new clones arrived through that gap.**

    "A count says 'worse' and never 'which'."

So a lesson stores the LIST of sites, and `lessons verify` names the ones that appeared.
"""

from __future__ import annotations

import subprocess

import pytest
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import inventory as INV


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _track(repo, **files: str):
    for name, text in files.items():
        (repo / name.replace("__", "/")).parent.mkdir(parents=True, exist_ok=True)
        (repo / name.replace("__", "/")).write_text(text)
    _git(repo, "add", "-A")


def _lessons(repo):
    return fold(EventLog(repo, "a1").read_all(), strict=False).lessons


# -- the scan --------------------------------------------------------------------------


def test_a_site_is_a_path_and_the_matched_text(repo):
    """NOT `path:line`. A line number churns on every edit above a site, which would
    manufacture a matching pair of "new site" and "fixed site" findings out of an unrelated
    change — and a ratchet that cries wolf is one that gets switched off."""
    assert run_cli(repo, "init")[0] == 0
    _track(repo, **{"a.py": "import os\ntry:\n    pass\nexcept OSError:\n    pass\n"})
    sites = INV.scan(repo, r"except OSError", ["*.py"])
    assert sites == ["a.py: except OSError:"]

    # Insert twenty lines ABOVE the site. Same site, because the identity has no line number.
    (repo / "a.py").write_text("# pad\n" * 20 + (repo / "a.py").read_text())
    _git(repo, "add", "-A")
    assert INV.scan(repo, r"except OSError", ["*.py"]) == sites


def test_an_invalid_pattern_raises_rather_than_scanning_nothing(repo):
    """An inventory that is empty because the regex did not compile reads exactly like one
    that is empty because the code is clean — and would ratchet every real site away."""
    assert run_cli(repo, "init")[0] == 0
    with pytest.raises(INV.PatternError):
        INV.scan(repo, r"except OSError(", ["*.py"])


def test_untracked_and_vendored_files_are_not_sites(repo):
    """Matches in `.venv` or a build tree are code nobody owns and will not act on."""
    assert run_cli(repo, "init")[0] == 0
    _track(repo, **{"real.py": "except OSError:\n"})
    # FORCE-ADDED into a skipped directory: some projects really do commit vendored deps,
    # and `git ls-files` returns them. Without SKIP_DIRS this would be a site nobody owns.
    (repo / ".venv").mkdir(exist_ok=True)
    (repo / ".venv" / "dep.py").write_text("except OSError:\n")
    _git(repo, "add", "-f", ".venv/dep.py")
    (repo / "scratch.py").write_text("except OSError:\n")  # untracked: also not a site
    sites = INV.scan(repo, r"except OSError", ["*.py"])
    assert sites == ["real.py: except OSError:"], sites


def test_globs_narrow_the_scan(repo):
    assert run_cli(repo, "init")[0] == 0
    _track(repo, **{"a.py": "TODO here\n", "b.md": "TODO here\n"})
    assert INV.scan(repo, "TODO", ["*.py"]) == ["a.py: TODO here"]
    assert len(INV.scan(repo, "TODO", [])) == 2, "no globs means every tracked file"


# -- filing stores the inventory --------------------------------------------------------


def test_filing_a_lesson_with_a_pattern_scans_and_stores_which_sites(repo):
    assert run_cli(repo, "init")[0] == 0
    _track(repo, **{"a.py": "except OSError:\n", "b.py": "except OSError:\n"})
    rc, out, _ = run_cli(
        repo,
        "lesson",
        "add",
        "--title",
        "no bare OSError",
        "--pattern",
        "except OSError",
        "--globs",
        "*.py",
    )
    assert rc == 0, out
    lesson = next(iter(_lessons(repo).values()))
    assert lesson.pattern == "except OSError"
    assert lesson.sites == ["a.py: except OSError:", "b.py: except OSError:"], lesson.sites


def test_a_lesson_with_an_invalid_pattern_is_REFUSED(repo):
    """Recording it with an empty inventory would arm a ratchet that permits everything."""
    assert run_cli(repo, "init")[0] == 0
    rc, out, _ = run_cli(repo, "lesson", "add", "--title", "bad", "--pattern", "foo(")
    assert rc != 0, out
    assert not _lessons(repo), "a lesson with an uncompilable pattern was recorded anyway"


def test_a_prose_lesson_needs_no_pattern(repo):
    """Most lessons are prose rules with nothing mechanical to check."""
    assert run_cli(repo, "init")[0] == 0
    assert run_cli(repo, "lesson", "add", "--title", "think", "--rule", "harder")[0] == 0
    lesson = next(iter(_lessons(repo).values()))
    assert lesson.pattern == "" and lesson.sites == []
    assert INV.diff(repo, lesson) is None, "a prose lesson must not produce findings"


# -- the ratchet -----------------------------------------------------------------------


def _file_lesson(repo, pattern=r"except OSError"):
    rc, out, _ = run_cli(
        repo,
        "lesson",
        "add",
        "--title",
        "no bare OSError",
        "--pattern",
        pattern,
        "--globs",
        "*.py",
    )
    assert rc == 0, out


def test_a_new_site_is_reported_BY_NAME(repo):
    """The whole point: not "2 sites now", but which file the mistake came back in."""
    assert run_cli(repo, "init")[0] == 0
    _track(repo, **{"a.py": "except OSError:\n"})
    _file_lesson(repo)
    _track(repo, **{"c.py": "except OSError:\n"})
    rc, out, _ = run_cli(repo, "lesson", "verify")
    assert rc == 1, out
    assert "c.py" in out, f"the new site was not NAMED: {out}"
    assert "NEW site" in out


def test_an_unchanged_inventory_passes(repo):
    """A check that fires on a clean tree is one nobody keeps running."""
    assert run_cli(repo, "init")[0] == 0
    _track(repo, **{"a.py": "except OSError:\n"})
    _file_lesson(repo)
    rc, out, _ = run_cli(repo, "lesson", "verify")
    assert rc == 0, out


def test_a_fixed_site_is_reported_as_progress_not_as_a_failure(repo):
    """The inventory may only shrink, and shrinking must not fail the check."""
    assert run_cli(repo, "init")[0] == 0
    _track(repo, **{"a.py": "except OSError:\n", "b.py": "except OSError:\n"})
    _file_lesson(repo)
    (repo / "b.py").write_text("except FileNotFoundError:\n")
    _git(repo, "add", "-A")
    rc, out, _ = run_cli(repo, "lesson", "verify")
    assert rc == 0, out
    assert "fixed" in out.lower(), out


def test_no_lesson_with_a_pattern_exits_2_and_is_not_a_pass(repo):
    """Reporting "all clear" for a corpus with zero ratchets is how a project convinces
    itself it has checks it does not have."""
    assert run_cli(repo, "init")[0] == 0
    assert run_cli(repo, "lesson", "add", "--title", "prose", "--rule", "r")[0] == 0
    rc, out, _ = run_cli(repo, "lesson", "verify")
    assert rc == 2, f"expected NOTHING (2), got {rc}: {out}"
    assert "nothing was checked" in out


def test_a_superseded_lesson_stops_being_checked(repo):
    """A rule the project has explicitly replaced must not keep failing a check — that is
    how a stale ratchet trains readers to ignore the rest."""
    assert run_cli(repo, "init")[0] == 0
    _track(repo, **{"a.py": "except OSError:\n"})
    _file_lesson(repo)
    old = next(iter(_lessons(repo)))
    _track(repo, **{"c.py": "except OSError:\n"})
    assert run_cli(repo, "lesson", "verify")[0] == 1, "fixture: it should be failing first"

    assert (
        run_cli(repo, "lesson", "add", "--title", "replaced", "--rule", "r", "--supersedes", old)[0]
        == 0
    )
    rc, out, _ = run_cli(repo, "lesson", "verify")
    assert rc == 2, f"a superseded lesson is still being checked: {rc} {out}"


def test_a_re_record_that_omits_the_pattern_does_not_disarm_the_ratchet(repo):
    """Fields merge rather than replace, so editing a lesson's prose cannot silently remove
    its inventory — the same rule `_h_lesson` already follows for title and rule."""
    assert run_cli(repo, "init")[0] == 0
    _track(repo, **{"a.py": "except OSError:\n"})
    _file_lesson(repo)
    lid = next(iter(_lessons(repo)))
    assert run_cli(repo, "lesson", "add", "--id", lid, "--title", "clearer title")[0] == 0
    lesson = _lessons(repo)[lid]
    assert lesson.pattern == "except OSError", "the pattern was dropped by a re-record"
    assert lesson.sites, "the inventory was dropped by a re-record"


def test_regressions_filters_superseded_lessons_itself(repo):
    """Called DIRECTLY with the raw lesson map, as any caller may.

    `lessons_verify` passes an already-filtered set, which made an inline `superseded_by`
    guard inside `regressions()` unreachable — a guard that cannot fail. The filter now
    lives once, in `checkable()`, and this exercises it through the path that does not
    pre-filter.
    """
    assert run_cli(repo, "init")[0] == 0
    _track(repo, **{"a.py": "except OSError:\n"})
    _file_lesson(repo)
    lid = next(iter(_lessons(repo)))
    _track(repo, **{"c.py": "except OSError:\n"})
    raw = _lessons(repo)
    assert [d.lesson for d in INV.regressions(repo, raw)] == [lid], "fixture: should regress"

    assert (
        run_cli(repo, "lesson", "add", "--title", "replaced", "--rule", "r", "--supersedes", lid)[0]
        == 0
    )
    assert INV.regressions(repo, _lessons(repo)) == [], (
        "regressions() reported a lesson the project has explicitly retired"
    )


def test_a_non_utf8_filename_does_not_break_the_scan(repo):
    """roborev on e8543f9, reproduced: `W.git(..., "ls-files", "-z")` decoded raw `-z`
    bytes as strict UTF-8, so one latin-1-named tracked file made `scan` -- and so every
    `ddflow lesson add` -- raise UnicodeDecodeError. The same bug had just been fixed in
    the commit hook; one shared reader (`W.git_paths`) now serves both."""
    import os

    weird = os.fsdecode(b"caf\xe9.py")
    (repo / weird).write_text("except OSError:\n    pass\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "odd name", "--no-verify")
    sites = INV.scan(repo, r"except OSError", ["*.py"])
    assert any(s.startswith(weird + ":") for s in sites), sites
