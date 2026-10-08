"""The release gate by impact (B-uni-compat-release-gate; D-compat; compatibility.md).

A release is numbered by the impact its changes DECLARE in the upgrade manifest, not bumped
by patch every time: a breaking change, while ddflow is 0.x, is a minor. `scripts/
release_impact.py` turns the declarations into a level and a version; `publish.yml` bumps by
it and `scripts/release.sh` refuses a version that is a smaller step. The functions are
tested on throwaway git repositories whose history is a release, then changes; the two
scripts are held to calling it.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "release_impact", ROOT / "scripts" / "release_impact.py"
)
assert _spec and _spec.loader
RI = importlib.util.module_from_spec(_spec)
sys.modules["release_impact"] = RI
_spec.loader.exec_module(RI)

MANIFEST = 'schema_version = 1\n\n[base]\nversion = "0.1.3"\nevent_kinds = []\n'
UNREL = Path("ddflow/templates/upgrade/unreleased")


def _fragment(name: str, impact: str) -> str:
    return (
        f'[[change]]\nkind = "feature"\nkey = "{name}"\nwhy = "{name}"\n'
        f'impact = "{impact}"\nenable = "ddflow {name}"\n'
    )


def git(repo: Path, *argv: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *argv], check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "proj"
    (r / UNREL).mkdir(parents=True)
    (r / "ddflow" / "templates" / "upgrade" / "changes.toml").write_text(MANIFEST)
    (r / "ddflow" / "__init__.py").write_text('__version__ = "0.1.5"\n')
    git(r, "init", "-q", "-b", "main")
    for k, v in (("user.email", "t@e.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        git(r, "config", k, v)
    return r


def commit(repo: Path, subject: str, files: dict[str, str] | None = None) -> str:
    for rel, text in (files or {}).items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", subject)
    return git(repo, "rev-parse", "HEAD").strip()


def fragment(repo: Path, name: str, impact: str, subject: str = "change") -> str:
    return commit(repo, subject, {f"{UNREL}/{name}.toml": _fragment(name, impact)})


def declared(repo: Path, version: str) -> None:
    commit(repo, f"declare {version}", {"ddflow/__init__.py": f'__version__ = "{version}"\n'})


# -- the last release ---------------------------------------------------------------------


def test_no_release_yet_has_no_base_and_is_a_patch(repo: Path) -> None:
    commit(repo, "start")
    assert RI.last_release(repo) is None
    assert RI.level_since(None, repo) == "patch"
    assert RI.check(None, repo)[0] is True


def test_the_last_release_is_the_newest_release_commit_or_tag(repo: Path) -> None:
    commit(repo, "start")
    first = commit(repo, "release 0.1.4")
    second = commit(repo, "release 0.1.5")
    assert RI.last_release(repo) == (second, "0.1.5")
    assert first != second
    commit(repo, "work")
    git(repo, "tag", "v0.1.9")  # a tag publishes out of band, and is newer than the commit
    assert RI.last_release(repo) == ("v0.1.9", "0.1.9")
    older = git(repo, "rev-parse", "HEAD~2").strip()
    git(repo, "tag", "v0.1.99", older)  # an OLDER commit's tag does not win by its number
    assert RI.last_release(repo) == ("v0.1.9", "0.1.9")


# -- the level ----------------------------------------------------------------------------


def test_only_additive_and_deprecating_changes_since_the_release_are_a_patch(repo: Path) -> None:
    fragment(repo, "old-breaking", "breaking")  # declared BEFORE the last release: not news
    commit(repo, "release 0.1.5")
    fragment(repo, "a", "additive")
    fragment(repo, "b", "deprecating")
    assert RI.level_since(None, repo) == "patch"


def test_a_breaking_change_since_the_release_is_a_minor_while_0x(repo: Path) -> None:
    commit(repo, "release 0.1.5")
    fragment(repo, "a", "additive")
    fragment(repo, "b", "breaking")
    assert RI.level_since(None, repo) == "minor"


def test_a_breaking_change_is_a_patch_from_1_0_on(repo: Path) -> None:
    assert RI.level_of([], "0.9.9") == "patch"
    commit(repo, "release 1.2.3")
    declared(repo, "1.2.3")
    fragment(repo, "b", "breaking")
    assert RI.level_since(None, repo) == "patch"


def test_cutting_a_version_neither_recounts_a_released_entry_nor_loses_a_new_one(
    repo: Path,
) -> None:
    fragment(repo, "kept", "breaking")
    commit(repo, "release 0.1.5")  # `kept` was announced before this release
    fragment(repo, "new", "additive")
    assert RI.level_since(None, repo) == "patch", "a breaking entry before the release is not news"
    base = repo / "ddflow" / "templates" / "upgrade"
    # `version cut` folds the fragments into a release block: the same entries, elsewhere
    folded = (
        MANIFEST
        + '\n[[release]]\nversion = "0.2.0"\ndate = "2026-10-08"\n\n'
        + _fragment("kept", "breaking").replace("[[change]]", "[[release.change]]")
        + _fragment("new", "additive").replace("[[change]]", "[[release.change]]")
    )
    (base / "changes.toml").write_text(folded)
    for f in (base / "unreleased").glob("*.toml"):
        f.unlink()
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "cut 0.2.0")
    assert RI.level_since(None, repo) == "patch", "folding `kept` must not make it news again"
    fragment(repo, "later", "breaking")
    assert RI.level_since(None, repo) == "minor", "a new breaking entry still counts after a cut"
    commit(repo, "release 0.2.0")
    assert RI.level_since(None, repo) == "patch", "nothing new since this release"


def test_an_entry_without_a_declared_impact_is_not_breaking(repo: Path) -> None:
    commit(repo, "release 0.1.5")
    body = '[[change]]\nkind = "feature"\nkey = "x"\nwhy = "x"\nenable = "ddflow x"\n'
    commit(repo, "x", {f"{UNREL}/x.toml": body})
    assert RI.level_since(None, repo) == "patch"


# -- the version --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "level", "want"),
    [("0.1.5", "patch", "0.1.6"), ("0.1.5", "minor", "0.2.0"), ("1.4.9", "minor", "1.5.0")],
)
def test_next_version(version: str, level: str, want: str) -> None:
    assert RI.next_version(version, level) == want


def test_next_version_refuses_what_it_cannot_number() -> None:
    for bad in (("0.1", "patch"), ("0.1.5", "huge"), ("x", "minor")):
        with pytest.raises(ValueError):
            RI.next_version(*bad)


def test_the_declared_version_must_be_the_step_the_impact_asks_for(repo: Path) -> None:
    commit(repo, "release 0.1.5")
    fragment(repo, "b", "breaking")
    declared(repo, "0.1.6")
    ok, why = RI.check(None, repo)
    assert not ok and "BREAKING" in why and "scripts/bump.sh minor" in why and "0.2.0" in why
    declared(repo, "0.2.0")
    assert RI.check(None, repo)[0] is True
    declared(repo, "0.3.1")
    assert RI.check(None, repo)[0] is True, "a bigger step than asked for is the operator's"


def test_a_patch_is_enough_without_a_breaking_change(repo: Path) -> None:
    commit(repo, "release 0.1.5")
    fragment(repo, "a", "additive")
    declared(repo, "0.1.6")
    assert RI.check(None, repo)[0] is True


def test_bump_sh_numbers_through_the_same_function_as_ci() -> None:
    text = (ROOT / "scripts" / "bump.sh").read_text()
    assert 'release_impact.py next "$CUR" "$WHAT"' in text
    assert "int(b) for b in bits" not in text, "a second definition of the arithmetic"
    assert RI.next_version("0.1.5", "major") == "1.0.0"


def test_the_command_line_numbers_a_version() -> None:
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "release_impact.py"), "next", "0.1.18", "minor"],
        capture_output=True,
        text=True,
    )
    assert (out.returncode, out.stdout.strip()) == (0, "0.2.0")
    bad = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "release_impact.py"), "next", "0.1.18", "huge"],
        capture_output=True,
        text=True,
    )
    assert bad.returncode == 1 and "unknown level" in bad.stderr


def test_a_candidate_steps_from_the_release_the_impact_was_measured_against(repo: Path) -> None:
    commit(repo, "release 0.1.5")
    fragment(repo, "b", "breaking")
    assert RI.candidate(repo) == ("0.2.0", "minor")  # declared 0.1.5 is published: a minor step
    git(repo, "tag", "v0.6.0")  # published out of band; main still declares 0.1.5
    fragment(repo, "c", "breaking")
    assert RI.candidate(repo) == ("0.7.0", "minor"), "the tag is the release it is measured from"


def test_a_release_made_by_hand_is_the_base_even_without_a_release_commit(repo: Path) -> None:
    commit(repo, "release 0.1.5")
    fragment(repo, "b", "breaking")  # announced before the hand release below
    declared(repo, "0.2.0")  # a person bumped, PyPI has it (CI published it as declared)
    assert RI.candidate(repo) == ("0.2.1", "patch"), "0.2.0 already carries the breaking entry"
    fragment(repo, "c", "breaking")
    assert RI.candidate(repo) == ("0.3.0", "minor"), "a new breaking entry steps from 0.2.0"


def test_a_candidate_with_nothing_to_compare_is_the_next_patch(tmp_path: Path) -> None:
    r = tmp_path / "fresh"
    (r / "ddflow").mkdir(parents=True)
    (r / "ddflow" / "__init__.py").write_text('__version__ = "0.1.5"\n')
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@e.com")
    git(r, "config", "user.name", "T")
    git(r, "config", "commit.gpgsign", "false")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "start")
    # the declaring commit is the base; nothing changed since, so a patch
    assert RI.candidate(r) == ("0.1.6", "patch")


def test_the_command_line_takes_its_base_by_either_spelling_and_fails_cleanly(repo: Path) -> None:
    script = str(ROOT / "scripts" / "release_impact.py")
    run = lambda *a: subprocess.run([sys.executable, script, *a], capture_output=True, text=True)  # noqa: E731
    assert run("check", "--base").returncode == 2, "a missing value is bad usage, not a traceback"
    assert "Traceback" not in run("check", "--base").stderr
    nope = run("level", "--base=no-such-ref")
    assert nope.returncode == 1 and "release_impact:" in nope.stderr
    equals = run("level", "--base=HEAD")
    spaced = run("level", "--base", "HEAD")
    assert equals.returncode == spaced.returncode == 0 and equals.stdout == spaced.stdout


def test_the_real_repository_has_a_level() -> None:
    assert RI.level_since() in ("patch", "minor")


# -- the workflow and the script call it -------------------------------------------------


def test_publish_bumps_by_the_declared_impact_not_always_by_patch() -> None:
    text = (ROOT / ".github" / "workflows" / "publish.yml").read_text()
    gate = text[text.index("  gate:") : text.index("  verify:")]
    assert "fetch-depth: 0" in gate, "the last release is found in history"
    assert "release_impact.py candidate" in gate and "release_impact.py level" not in gate
    assert "bumped to $version and committed" in gate
    # a candidate PyPI already holds moves on by patch, through the same helper
    assert 'release_impact.py next "$next" patch' in gate
    assert "uv run python" not in gate, "uv run without --frozen could rewrite uv.lock"
    assert 'c + 1}")\' "$next"' not in gate, "the hard-coded patch increment is gone"
    # a version published as declared must be a big enough step; a failing `base` is not a patch
    assert gate.count("declared_ok") >= 3 and "release_impact.py check" in gate
    assert "could not number the release" in gate


def test_release_sh_checks_the_impact_and_the_surface_before_the_suite() -> None:
    text = (ROOT / "scripts" / "release.sh").read_text()
    impact = text.index("release_impact.py check")
    surface = text.index("tests/compat/test_surface.py")
    suite = text.index("pytest tests/ -q")
    assert impact < suite and surface < suite, "cheap refusals come before the twelve-minute suite"
    for option in ("alias", "upgrade manifest", 'impact = "breaking"', "DECLARED"):
        assert option in text[surface:suite], f"the refusal does not name its option: {option}"
    assert subprocess.run(["sh", "-n", str(ROOT / "scripts" / "release.sh")]).returncode == 0


def test_the_compatibility_page_names_what_enforces_the_release_gate() -> None:
    text = (ROOT / "docs" / "ddflow" / "compatibility.md").read_text()
    assert "scripts/release_impact.py" in text and "tests/test_release_gate.py" in text
