"""B17: a commit that removes or renames a name must not leave a doc line naming it.

A stale page is worse than a missing one: a reader who finds nothing reads the code, a
reader who finds a wrong default trusts it. Most of these run through a real `git commit`
and the installed hook, because the hook is the only place this is enforced and a unit
test of the detector would not show that the hook calls it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git_proc as _git

from ddflow.services import docsync as D

DOC_GLOBS = ["**/*.md", "**/*.rst", "**/*.adoc"]


def _commit(repo: Path, *paths: str) -> subprocess.CompletedProcess:
    _git(repo, "add", "-A", "--", *paths)
    return _git(repo, "commit", "-m", "change")


def _config(repo: Path, mode: str = "block", extra: str = "") -> None:
    # The lease and view policies are OFF so nothing but the doc check can refuse.
    (repo / ".ddflow" / "config.toml").write_text(
        '[enforce]\ncommit_without_lease = "off"\ngenerated_views = "off"\n'
        f'stale_docs = "{mode}"\n{extra}'
    )


@pytest.fixture
def adopted(repo):
    run_cli(repo, "adopt", "--agents", "claude")
    _config(repo)
    (repo / "src").mkdir()
    (repo / "src" / "knobs.py").write_text(
        "lease_ttl_s: int = 900\n\ndef renew_lease(holder):\n    return holder\n"
    )
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "guide.md").write_text(
        "# Guide\n\nCall `renew_lease` before `lease_ttl_s` runs out (default 900).\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "scaffold", "--no-verify")
    return repo


def _rename_function(repo: Path) -> None:
    src = repo / "src" / "knobs.py"
    src.write_text(src.read_text().replace("renew_lease", "extend_lease"))


def test_renaming_a_function_the_docs_name_is_refused_and_the_line_named(adopted):
    _rename_function(adopted)
    r = _commit(adopted, "src")
    assert r.returncode != 0, "a rename leaving a stale doc line was committed"
    assert "docs/guide.md:3" in r.stderr and "renew_lease" in r.stderr, r.stderr


def test_updating_the_doc_in_the_same_commit_passes(adopted):
    _rename_function(adopted)
    doc = adopted / "docs" / "guide.md"
    doc.write_text(doc.read_text().replace("renew_lease", "extend_lease"))
    r = _commit(adopted, "src", "docs")
    assert r.returncode == 0, r.stderr


def test_a_doc_line_the_commit_adds_may_name_the_old_name(adopted):
    """'renamed from `renew_lease`' is the commit documenting itself."""
    _rename_function(adopted)
    doc = adopted / "docs" / "guide.md"
    doc.write_text(
        "# Guide\n\nCall `extend_lease` before `lease_ttl_s` runs out (default 900).\n"
        "It was renamed from `renew_lease`.\n"
    )
    r = _commit(adopted, "src", "docs")
    assert r.returncode == 0, r.stderr


def test_a_name_still_used_elsewhere_was_moved_not_removed(adopted):
    (adopted / "src" / "other.py").write_text("from knobs import renew_lease\n")
    _git(adopted, "add", "-A")
    _git(adopted, "commit", "-qm", "second user", "--no-verify")
    _rename_function(adopted)  # knobs.py drops it; other.py still names it
    r = _commit(adopted, "src/knobs.py")
    assert r.returncode == 0, r.stderr


def test_changing_a_default_the_docs_quote_is_refused(adopted):
    src = adopted / "src" / "knobs.py"
    src.write_text(src.read_text().replace("= 900", "= 1800"))
    r = _commit(adopted, "src")
    assert r.returncode != 0, "a changed default still quoted by the docs was committed"
    assert "lease_ttl_s = 900" in r.stderr, r.stderr


def test_deleting_a_file_the_docs_cite_is_refused(adopted):
    (adopted / "src" / "probe_nfs_locks.py").write_text("print(1)\n")
    (adopted / "docs" / "probes.md").write_text("Run `src/probe_nfs_locks.py`.\n")
    _git(adopted, "add", "-A")
    _git(adopted, "commit", "-qm", "probe", "--no-verify")
    _git(adopted, "rm", "-q", "src/probe_nfs_locks.py")
    r = _git(adopted, "commit", "-m", "drop probe")
    assert r.returncode != 0, "deleting a cited file was committed"
    assert "docs/probes.md:1" in r.stderr and "probe_nfs_locks" in r.stderr, r.stderr


def test_an_excluded_page_may_remember_the_old_name(adopted):
    _config(adopted, extra='doc_exclude = ["docs/**"]\n')
    _git(adopted, "add", "-A")
    _git(adopted, "commit", "-qm", "exclude docs", "--no-verify")
    _rename_function(adopted)
    r = _commit(adopted, "src")
    assert r.returncode == 0, r.stderr


def test_warn_prints_and_allows(adopted):
    _config(adopted, mode="warn")
    _git(adopted, "add", "-A")
    _git(adopted, "commit", "-qm", "warn", "--no-verify")
    _rename_function(adopted)
    r = _commit(adopted, "src")
    assert r.returncode == 0, r.stderr
    assert "renew_lease" in r.stderr and "warning only" in r.stderr


def test_the_default_policy_warns_rather_than_refuses():
    from ddflow.config import Config

    assert Config().enforce.stale_docs == "warn"


def test_git_failing_is_not_a_pass(adopted, monkeypatch):
    """'Could not tell' must never read as 'nothing stale'."""
    from ddflow.config import Config
    from ddflow.services import enforce as E

    monkeypatch.setattr(D, "staged_diff", lambda repo: None)
    code, msg = E.check_docs(adopted, Config.load(adopted))
    assert code == 1 and "could not run" in msg
    # Under 'warn' it is allowed, but REPORTED -- and must not claim to refuse (critic on
    # B17: the message said "Refusing" while the exit code allowed the commit).
    _config(adopted, mode="warn")
    code, msg = E.check_docs(adopted, Config.load(adopted))
    assert code == 0 and "could not run" in msg and "Refusing" not in msg


@pytest.mark.parametrize(
    ("line", "want"),
    [
        ('print("\\nBlocked")', set()),  # an escape sequence is not camelCase
        ("merge the lease", set()),  # plain words are prose
        ("run --dry-run then fold_events(x)", {"--dry-run", "fold_events"}),
        ("isReady = MAX_LEASE", {"isReady", "MAX_LEASE"}),
    ],
)
def test_only_identifier_shaped_tokens_count(line, want):
    assert set(D.TOKEN.findall(line)) == want


def _doc_globs_agree_with_git(repo: Path, globs: list[str]) -> set[str]:
    """Python's idea of a doc must be exactly the set git grep searched. Returns git's set."""
    git = set(_git(repo, "ls-files", "--", *(f":(glob){g}" for g in globs)).stdout.split())
    ours = {p for p in _git(repo, "ls-files").stdout.split() if D.is_doc(p, globs)}
    assert ours == git
    return git


def test_the_doc_globs_mean_what_git_means_by_them(repo):
    for p in ("README.md", "docs/a.md", "docs/sub/b.rst", "src/x.py", "notes.txt", "a/b.txt"):
        (repo / p).parent.mkdir(parents=True, exist_ok=True)
        (repo / p).write_text("x\n")
    _git(repo, "add", "-A")
    _doc_globs_agree_with_git(repo, ["**/*.md", "docs/**", "*.txt"])


def test_many_removed_names_are_all_checked_for_liveness(repo):
    """git grep -o under-reports with many -F patterns (lesson L5808fe73f8): a live
    name reported gone would be flagged in every doc that mentions it."""
    names = [f"name_{i:03d}" for i in range(250)]
    (repo / "live.py").write_text("\n".join(f"x.{n} = {n}_v" for n in names) + "\n")
    _git(repo, "add", "-A")
    assert D._not_live(repo, names, DOC_GLOBS) == []


def test_a_removed_line_starting_with_dashes_is_content_not_a_header(adopted):
    """Regression (rubber-duck on B17): `-- purge_cache` removed in -U0 is the diff line
    `--- purge_cache`, which the parser read as a file header and dropped."""
    (adopted / "src" / "q.sql").write_text("SELECT 1;\n-- purge_cache helper\nSELECT 2;\n")
    (adopted / "docs" / "sql.md").write_text("Run `purge_cache` nightly.\n")
    _git(adopted, "add", "-A")
    _git(adopted, "commit", "-qm", "sql", "--no-verify")
    (adopted / "src" / "q.sql").write_text("SELECT 1;\nSELECT 2;\n")
    r = _commit(adopted, "src")
    assert r.returncode != 0 and "docs/sql.md:1" in r.stderr, r.stderr


def test_an_added_line_starting_with_pluses_does_not_derail_the_file(adopted):
    """Regression (rubber-duck on B17): an added `++ x` doc line is the diff line
    `+++ x`, which re-ran the header logic mid-hunk and flipped the doc state -- the
    doc's next line was then taken as CODE re-adding the removed name."""
    _rename_function(adopted)
    doc = adopted / "docs" / "guide.md"
    doc.write_text(doc.read_text() + "++ a decoy line\nSee `renew_lease` above.\n")
    r = _commit(adopted, "src", "docs")
    assert r.returncode != 0, "the rename was cancelled out by a doc line read as code"
    assert "docs/guide.md:3" in r.stderr, r.stderr


def test_a_build_file_ending_in_txt_is_code_not_documentation(repo):
    """Bug hunt on B17: `**/*.txt` in the default doc globs made CMakeLists.txt a doc,
    so a target it removed was never a removal and a README naming it stayed stale."""
    from ddflow.config import Config

    run_cli(repo, "adopt", "--agents", "claude")
    _config(repo)
    (repo / "CMakeLists.txt").write_text("add_executable(probe_runner main.c)\n")
    (repo / "README.md").write_text("# proj\n\nBuild `probe_runner` first.\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "cmake", "--no-verify")
    (repo / "CMakeLists.txt").write_text("add_executable(main_runner main.c)\n")
    r = _commit(repo, "CMakeLists.txt")
    assert r.returncode != 0 and "README.md:3" in r.stderr, r.stderr
    assert not D.is_doc("CMakeLists.txt", Config().enforce.doc_globs)


@pytest.mark.parametrize(
    ("before", "after", "doc", "stale"),
    [
        # A sentence-ending period is punctuation, not part of the old value.
        ("foo_bar = 30", "foo_bar = 40", "The foo_bar default is 30.", True),
        ("foo_bar = 30", "foo_bar = 40", "foo_bar may also be 30.5", False),
        # A quoted default may hold the characters that end an unquoted one.
        ('foo_bar = "a,b"', 'foo_bar = "c"', 'Set foo_bar = "a,b" by default.', True),
        ('foo_bar = "a#b"', 'foo_bar = "c"', 'Set foo_bar = "a#b" by default.', True),
        # Changing only the quote style does not change the default.
        ('foo_bar = "x"', "foo_bar = 'x'", 'Set foo_bar = "x".', False),
        # A hyphen joins words: `foo` is not the value inside `foo-bar`.
        ('foo_bar = "foo"', 'foo_bar = "bar"', "foo_bar and foo-bar are related.", False),
        ('foo_bar = "foo"', 'foo_bar = "bar"', 'foo_bar is "foo" by default.', True),
        # An empty old value cannot be quoted by a page, and as a pattern it matches anywhere.
        ('foo_bar = ""', 'foo_bar = "/x"', "The `foo_bar` knob joins paths.", False),
    ],
)
def test_a_changed_default_is_matched_as_a_value_not_a_substring(repo, before, after, doc, stale):
    """Regressions (cross-family critic on B17): the old value's boundaries and the
    default parser, each reproduced before the fix."""
    (repo / "conf.py").write_text(before + "\n")
    (repo / "README.md").write_text(doc + "\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "a", "--no-verify")
    (repo / "conf.py").write_text(after + "\n")
    _git(repo, "add", "conf.py")
    hits = D.stale_mentions(repo, DOC_GLOBS, [])
    assert hits is not None
    assert bool(hits) is stale, hits


def test_a_long_flag_is_found_by_a_word_grep(adopted):
    """The critic's claim that `git grep -w` cannot match `--long-flag` was probed and is
    false: the boundary git checks is the character BEFORE the leading dash."""
    (adopted / "src" / "cli.py").write_text('ARGS = ["--long-flag"]\n')
    (adopted / "docs" / "cli.md").write_text("Use `--long-flag` to enable it.\n")
    _git(adopted, "add", "-A")
    _git(adopted, "commit", "-qm", "cli", "--no-verify")
    (adopted / "src" / "cli.py").write_text("ARGS = []\n")
    r = _commit(adopted, "src")
    assert r.returncode != 0 and "docs/cli.md:1" in r.stderr, r.stderr


# -- Mutation cadence (2026-09-28): five mutants of services/docsync.py survived
# tests/test_doc_sync.py. One test each, below; every one FAILS with its mutant applied
# and passes on the real code (the mutation is what the test is built to catch).


def test_a_question_mark_in_a_doc_glob_matches_exactly_one_character(repo):
    """Mutant: the `?` branch of glob_regex disabled. `?` then becomes a literal, so a glob
    like `docs/?.md` stops being the set of paths git grep searched as docs."""
    for p in ("docs/a.md", "docs/ab.md", "docs/deep/x.md"):
        (repo / p).parent.mkdir(parents=True, exist_ok=True)
        (repo / p).write_text("x\n")
    _git(repo, "add", "-A")
    assert _doc_globs_agree_with_git(repo, ["docs/?.md"]) == {"docs/a.md"}


def test_a_header_path_is_unquoted_only_when_git_quoted_it():
    """Mutant: the `_unquote` guard disabled. git C-quotes a name holding a backslash even
    with core.quotepath=false, and the quoted form carries an escape the path does not --
    but an unquoted path must NOT be run through the escaper (`\\b` is a backspace to it)."""
    assert D._unquote('"src/a\\\\b.py"') == "src/a\\b.py"  # quoted: decode git's escape
    assert D._unquote("src/a\\b.py") == "src/a\\b.py"  # unquoted: left alone


def test_dev_null_in_a_diff_header_is_not_a_deleted_path(adopted):
    """Mutant: `_header_path`'s `/dev/null` -> None disabled. A deleted page's own header is
    `+++ /dev/null`; read as a path it makes the DOC look like code, so the page's
    identifier-shaped stem is announced as a removed CODE name and another page that merely
    names it is reported stale. Deleting a page changes docs; it removes no code name."""
    assert D._header_path("+++ /dev/null", "b/") is None  # the mechanism, pinned
    (adopted / "docs" / "probe_runner.md").write_text("# Probe runner\n")
    (adopted / "docs" / "notes.md").write_text("See `probe_runner` for details.\n")
    _git(adopted, "add", "-A")
    _git(adopted, "commit", "-qm", "page", "--no-verify")
    _git(adopted, "rm", "-q", "docs/probe_runner.md")
    r = _git(adopted, "commit", "-m", "drop page")
    assert r.returncode == 0, r.stderr


def test_renaming_a_file_the_docs_cite_is_refused(adopted):
    """Mutant: the rename from/to buckets swapped. A pure rename has only `rename from` and
    `rename to` lines, so swapping them makes the OLD name read as added and the NEW name as
    removed -- the stale citation of the old name is then never reported."""
    (adopted / "src" / "probe_runner.py").write_text("print('hi')\n")
    (adopted / "docs" / "probes.md").write_text("Run `src/probe_runner.py`.\n")
    _git(adopted, "add", "-A")
    _git(adopted, "commit", "-qm", "probe", "--no-verify")
    _git(adopted, "mv", "src/probe_runner.py", "src/runner_probe.py")
    r = _git(adopted, "commit", "-m", "rename probe")
    assert r.returncode != 0, "a pure rename leaving a stale doc line was committed"
    assert "docs/probes.md:1" in r.stderr and "probe_runner" in r.stderr, r.stderr


def test_a_real_git_diff_failure_is_not_a_pass(tmp_path):
    """Mutant: staged_diff's own `returncode != 0 -> None` removed. test_git_failing_is_not
    _a_pass monkeypatches staged_diff itself, so the real failure path is never exercised:
    make git actually fail and require the detector to say 'could not tell', never 'clean'."""
    missing = tmp_path / "not-a-repo"
    assert D.staged_diff(missing) is None
    assert D.stale_mentions(missing, DOC_GLOBS, []) is None
