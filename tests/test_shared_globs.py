"""Files every item touches may be held by many at once (B07878037ab, D-shared-globs).

CHANGELOG, a research log, a README, a regenerated config: every parallel item edits
them. Exclusive leases made them collide -- claims refused, or agents committing them
unleased and the hook's warning turning into noise, with the real conflicts surfacing
only at merge. Two settings now:

- `[lease].append_only_globs`: shared, and ddflow writes `<glob> merge=union` to
  `.gitattributes`, so two items' added lines both survive the merge;
- `[lease].shared_globs`: shared, but NOT unioned -- a generated file interleaved line by
  line is corrupt. It must be regenerated after merging, and doctor says so when no
  merge strategy is declared for it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import run_cli

from ddflow.api import items as AI
from ddflow.api import lifecycle as A
from ddflow.config import Config
from ddflow.core import outcome as O
from ddflow.services import adopt as AD
from ddflow.services import shared_files as SF

ALICE, BOB = "agent-alice", "agent-bob"


def _project(repo: Path, *, append: str = "", shared: str = "") -> None:
    run_cli(repo, "init")
    if append:
        code, _o, err = run_cli(repo, "config", "--set", "lease.append_only_globs", append)
        assert code == O.OK, err
    if shared:
        code, _o, err = run_cli(repo, "config", "--set", "lease.shared_globs", shared)
        assert code == O.OK, err


def _attributes(repo: Path) -> list[str]:
    p = repo / ".gitattributes"
    return p.read_text().splitlines() if p.exists() else []


def test_two_items_may_both_claim_an_append_only_file(repo):
    _project(repo, append='["docs/CHANGELOG.md"]')
    run_cli(repo, "task", "add", "T1", "--globs", "src/a.py,docs/CHANGELOG.md")
    run_cli(repo, "task", "add", "T2", "--globs", "src/b.py,docs/CHANGELOG.md")
    assert A.claim(repo, "T1", no_worktree=True, agent=ALICE).ok
    out = A.claim(repo, "T2", no_worktree=True, agent=BOB)
    assert out.ok, out.reason


def test_a_shared_generated_file_is_shared_too_and_widening_onto_it_is_not_refused(repo):
    _project(repo, shared='["configs/default.toml"]')
    run_cli(repo, "task", "add", "T1", "--globs", "src/a.py,configs/default.toml")
    run_cli(repo, "task", "add", "T2", "--globs", "src/b.py")
    assert A.claim(repo, "T1", no_worktree=True, agent=ALICE).ok
    assert A.claim(repo, "T2", no_worktree=True, agent=BOB).ok
    out = AI.update(repo, "T2", AI.ItemEdit(globs=["src/b.py", "configs/default.toml"]))
    assert out.ok, out.reason


def test_everything_else_still_collides(repo):
    """Only paths INSIDE a shared glob are exempt: a wide glob that merely overlaps one
    still claims the rest of the directory."""
    _project(repo, append='["docs/CHANGELOG.md"]')
    run_cli(repo, "task", "add", "T1", "--globs", "docs/**")
    run_cli(repo, "task", "add", "T2", "--globs", "docs/guide.md")
    assert A.claim(repo, "T1", no_worktree=True, agent=ALICE).ok
    out = A.claim(repo, "T2", no_worktree=True, agent=BOB)
    assert out.exit == O.REFUSED, out.reason


def test_next_offers_an_item_whose_only_overlap_is_shared(repo):
    _project(repo, append='["docs/CHANGELOG.md"]')
    run_cli(repo, "task", "add", "T1", "--globs", "docs/CHANGELOG.md,a.py")
    run_cli(repo, "task", "add", "T2", "--globs", "docs/CHANGELOG.md,b.py")
    assert A.claim(repo, "T1", no_worktree=True, agent=ALICE).ok
    code, out, err = run_cli(repo, "next", agent=BOB)
    assert code == O.OK and "T2" in out, out + err


def _hook(repo: Path, agent: str) -> str:
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        "DDFLOW_AGENT": agent,
    }
    p = subprocess.run(
        [sys.executable, "-m", "ddflow", "--repo", str(repo), "hooks", "check-commit"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    return p.stdout + p.stderr


def test_the_hook_counts_a_shared_path_covered_for_any_live_holder(repo):
    _project(repo, append='["docs/CHANGELOG.md"]')
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "ddflow"], check=True)
    run_cli(repo, "task", "add", "T1", "--globs", "src/a.py")
    (repo / "docs").mkdir()
    (repo / "docs" / "CHANGELOG.md").write_text("- a change\n")
    subprocess.run(["git", "-C", str(repo), "add", "docs/CHANGELOG.md"], check=True)
    assert "not covered" in _hook(repo, ALICE), "no lease at all: still uncovered"
    assert A.claim(repo, "T1", no_worktree=True, agent=ALICE).ok
    assert "not covered" not in _hook(repo, ALICE)


def test_setting_append_only_globs_writes_the_union_line_once_and_says_so(repo):
    run_cli(repo, "init")
    code, out, err = run_cli(
        repo, "config", "--set", "lease.append_only_globs", '["docs/CHANGELOG.md"]'
    )
    assert code == O.OK, err
    assert "docs/CHANGELOG.md merge=union" in _attributes(repo)
    assert "docs/CHANGELOG.md merge=union" in out, out
    code, out, err = run_cli(
        repo, "config", "--set", "lease.append_only_globs", '["docs/CHANGELOG.md"]'
    )
    assert code == O.OK, err
    assert _attributes(repo).count("docs/CHANGELOG.md merge=union") == 1
    assert ".gitattributes" not in out, "nothing was added the second time"


def test_a_generated_shared_file_never_gets_merge_union(repo):
    _project(repo, shared='["configs/default.toml"]')
    assert not any(ln.startswith("configs/default.toml") for ln in _attributes(repo))
    assert SF.sync_attributes(repo) == []


def test_init_resyncs_a_hand_edited_config(repo):
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(
        cfg.read_text().replace("[lease]\n", '[lease]\nappend_only_globs = ["RESEARCH.md"]\n', 1)
    )
    actions = AD.init_files(repo)
    assert "RESEARCH.md merge=union" in _attributes(repo)
    assert any("RESEARCH.md merge=union" in a for a in actions), actions


def test_findings_name_a_missing_union_line_and_an_unmerged_generated_file(repo):
    _project(repo, append='["docs/CHANGELOG.md"]', shared='["configs/default.toml"]')
    cfg = Config.load(repo)
    problems, notes = SF.findings(repo, cfg)
    assert problems == []
    assert any("configs/default.toml" in n and "merge" in n for n in notes), notes
    (repo / ".gitattributes").write_text("")
    problems, _notes = SF.findings(repo, cfg)
    assert any("docs/CHANGELOG.md" in p and "merge=union" in p for p in problems), problems
    (repo / ".gitattributes").write_text("configs/default.toml merge=ours\n")
    _p, notes = SF.findings(repo, cfg)
    assert not any("configs/default.toml" in n for n in notes), notes


def test_a_local_only_append_only_glob_writes_no_tracked_rule(repo):
    run_cli(repo, "init")
    before = _attributes(repo)
    code, out, err = run_cli(
        repo, "config", "--local", "--set", "lease.append_only_globs", '["NOTES.md"]'
    )
    assert code == O.OK, err
    assert _attributes(repo) == before, "a machine-local setting wrote a committed rule"
    assert ".gitattributes" not in out


def test_a_bare_merge_attribute_is_not_a_union(repo):
    run_cli(repo, "init")
    with (repo / ".gitattributes").open("a") as f:
        f.write("docs/CHANGELOG.md merge\n")
    code, _o, err = run_cli(
        repo, "config", "--set", "lease.append_only_globs", '["docs/CHANGELOG.md"]'
    )
    assert code == O.OK, err
    assert "docs/CHANGELOG.md merge=union" in _attributes(repo)


def test_shared_globs_match_as_git_matches_the_attributes_line():
    from ddflow.core.schedule import is_shared

    assert is_shared("CHANGELOG.md", ["**/CHANGELOG.md"])  # git's **/ includes the root
    assert is_shared("pkg/CHANGELOG.md", ["CHANGELOG.md"])  # no slash: any depth
    assert is_shared("docs/b.md", ["docs/*.md"])
    assert not is_shared("docs/a/b.md", ["docs/*.md"])  # git's * stops at /


def test_a_driver_the_project_chose_is_left_alone_and_named(repo):
    run_cli(repo, "init")
    with (repo / ".gitattributes").open("a") as f:
        f.write("CHANGELOG.md merge=union\n*.md merge=ours\n")
    before = _attributes(repo)
    code, _o, err = run_cli(repo, "config", "--set", "lease.append_only_globs", '["CHANGELOG.md"]')
    assert code == O.OK, err
    assert _attributes(repo) == before, "rewrote the project's own driver"
    problems, notes = SF.findings(repo, Config.load(repo))
    assert problems == [] and any("merge=ours" in n for n in notes), (problems, notes)


def test_a_driver_from_a_broader_pattern_counts(repo):
    run_cli(repo, "init")
    with (repo / ".gitattributes").open("a") as f:
        f.write("*.md merge=union\n")
    before = _attributes(repo)
    assert run_cli(repo, "config", "--set", "lease.append_only_globs", '["NEWS.md"]')[0] == 0
    assert _attributes(repo) == before
    assert SF.findings(repo, Config.load(repo)) == ([], [])


def test_append_only_written_as_one_string_is_one_glob(repo):
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(
        cfg.read_text().replace("[lease]\n", '[lease]\nappend_only_globs = "NEWS.md"\n', 1)
    )
    assert SF.sync_attributes(repo) == ["NEWS.md merge=union"]


def test_doctor_reports_a_missing_union_line_and_notes_an_unmerged_generated_file(repo):
    _project(repo, append='["docs/CHANGELOG.md"]', shared='["configs/default.toml"]')
    (repo / ".gitattributes").write_text("")
    code, out, err = run_cli(repo, "doctor")
    text = out + err
    assert code != O.OK, text
    assert "docs/CHANGELOG.md" in text and "merge=union" in text, text
    assert "configs/default.toml" in text and "regenerate" in text, text


def test_git_glob_edge_cases_match_as_git_does():
    from ddflow.core.schedule import is_shared

    assert not is_shared("docs/2024/CHANGELOG.md", ["docs/**.md"])  # ** not on a boundary
    assert is_shared("docs/CHANGELOG.md", ["docs/**.md"])
    assert is_shared("a/b/c.md", ["a/**"]) and is_shared("x/a/y/c.md", ["**/a/**/c.md"])
    assert is_shared("file5.txt", ["file[!0-9].txt"]) is False
    assert is_shared("filex.txt", ["file[!0-9].txt"])


def test_a_local_edit_writes_no_tracked_rule_even_when_one_is_missing(repo):
    _project(repo, append='["docs/CHANGELOG.md"]')
    (repo / ".gitattributes").write_text("")
    code, _o, err = run_cli(repo, "config", "--local", "--set", "lease.ttl_s", "3600")
    assert code == O.OK, err
    assert _attributes(repo) == []


def test_a_character_class_glob_sees_its_own_union_line(repo):
    run_cli(repo, "init")
    (repo / "CHANGELOG.md").write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", "CHANGELOG.md"], check=True)
    with (repo / ".gitattributes").open("a") as f:
        f.write("[Cc]HANGELOG.md merge=ours\n")
    before = _attributes(repo)
    assert (
        run_cli(repo, "config", "--set", "lease.append_only_globs", '["[Cc]HANGELOG.md"]')[0] == 0
    )
    assert _attributes(repo) == before, "wrote over the project's own driver"


def test_a_glob_with_a_space_is_reported_not_written_broken(repo):
    _project(repo, append='["docs/Change Log.md"]')
    assert not any("Change" in ln for ln in _attributes(repo))
    problems, _n = SF.findings(repo, Config.load(repo))
    assert any("docs/Change?Log.md" in p for p in problems), problems


def test_a_caret_class_negates_as_in_git():
    from ddflow.core.schedule import is_shared

    assert is_shared("filex.txt", ["file[^0-9].txt"])
    assert not is_shared("file5.txt", ["file[^0-9].txt"])


def test_a_driver_on_one_covered_file_does_not_stand_for_the_rest(repo):
    run_cli(repo, "init")
    (repo / "docs").mkdir()
    for name in ("README.md", "guide.md"):
        (repo / "docs" / name).write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", "docs"], check=True)
    with (repo / ".gitattributes").open("a") as f:
        f.write("docs/README.md merge=ours\n")
    assert run_cli(repo, "config", "--set", "lease.append_only_globs", '["docs/*.md"]')[0] == 0
    assert "docs/*.md merge=union" in _attributes(repo)


def test_a_bracket_as_the_first_class_member_is_a_member():
    from ddflow.core.schedule import is_shared

    assert is_shared("a].txt", ["a[]].txt"])
    assert is_shared("ax.txt", ["a[^]].txt"]) and not is_shared("a].txt", ["a[^]].txt"])
    assert not is_shared("x", ["[^]"])  # unclosed: literal, and no crash


def test_doctor_names_a_shared_glob_it_cannot_read(repo):
    _project(repo, shared='["[z-a]"]')
    problems, _n = SF.findings(repo, Config.load(repo))
    assert any("[z-a]" in p and "cannot be read" in p for p in problems), problems


def _merge_attr(repo: Path, path: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo), "check-attr", "merge", "--", path],
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout.strip().rsplit(": ", 1)[-1]


def test_a_narrower_driver_the_project_set_keeps_winning(repo):
    """Operator 2026-10-01: the NARROWER driver wins. git applies the last matching line,
    so the union line for the glob goes BEFORE the narrower one."""
    run_cli(repo, "init")
    (repo / "docs").mkdir()
    for name in ("README.md", "guide.md"):
        (repo / "docs" / name).write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", "docs"], check=True)
    with (repo / ".gitattributes").open("a") as f:
        f.write("docs/README.md merge=ours\n")
    assert run_cli(repo, "config", "--set", "lease.append_only_globs", '["docs/*.md"]')[0] == 0
    assert _merge_attr(repo, "docs/README.md") == "ours"
    assert _merge_attr(repo, "docs/guide.md") == "union"
    lines = _attributes(repo)
    assert lines.count("docs/*.md merge=union") == 1
    assert lines.index("docs/*.md merge=union") < lines.index("docs/README.md merge=ours")
    # Idempotent, and doctor has nothing to say about the narrower file.
    assert SF.sync_attributes(repo) == []
    assert SF.findings(repo, Config.load(repo)) == ([], [])


def test_a_union_line_08af811_wrote_after_a_narrower_driver_is_moved_before_it(repo):
    run_cli(repo, "init")
    (repo / "docs").mkdir()
    for name in ("README.md", "guide.md"):
        (repo / "docs" / name).write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", "docs"], check=True)
    with (repo / ".gitattributes").open("a") as f:
        f.write("docs/README.md merge=ours\ndocs/*.md merge=union\n")  # the old order
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(
        cfg.read_text().replace("[lease]\n", '[lease]\nappend_only_globs = ["docs/*.md"]\n', 1)
    )
    problems, _n = SF.findings(repo, Config.load(repo))
    assert any("AFTER a narrower" in p for p in problems), problems
    assert SF.sync_attributes(repo) == ["docs/*.md merge=union"]
    lines = _attributes(repo)
    assert lines.count("docs/*.md merge=union") == 1
    assert _merge_attr(repo, "docs/README.md") == "ours"
    assert _merge_attr(repo, "docs/guide.md") == "union"
    assert SF.sync_attributes(repo) == [] and SF.findings(repo, Config.load(repo)) == ([], [])


def test_a_broader_earlier_line_does_not_pull_the_union_line_before_it(repo):
    run_cli(repo, "init")
    (repo / "docs").mkdir()
    (repo / "docs" / "guide.md").write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", "docs"], check=True)
    with (repo / ".gitattributes").open("a") as f:
        f.write("*.md merge=binary\ndocs/*.md merge=union\n")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(
        cfg.read_text().replace("[lease]\n", '[lease]\nappend_only_globs = ["docs/*.md"]\n', 1)
    )
    assert SF.sync_attributes(repo) == []
    assert _merge_attr(repo, "docs/guide.md") == "union"


def test_a_broader_line_between_narrower_and_union_is_not_leapfrogged(repo):
    run_cli(repo, "init")
    (repo / "docs").mkdir()
    for name in ("README.md", "guide.md"):
        (repo / "docs" / name).write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", "docs"], check=True)
    with (repo / ".gitattributes").open("a") as f:
        f.write("docs/README.md merge=ours\n*.md merge=binary\ndocs/*.md merge=union\n")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(
        cfg.read_text().replace("[lease]\n", '[lease]\nappend_only_globs = ["docs/*.md"]\n', 1)
    )
    SF.sync_attributes(repo)
    assert _merge_attr(repo, "docs/guide.md") == "union", _attributes(repo)


def _declare(repo: Path, glob: str) -> None:
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(
        cfg.read_text().replace("[lease]\n", f'[lease]\nappend_only_globs = ["{glob}"]\n', 1)
    )


def test_a_wildcard_narrower_rule_covering_every_file_today_still_wins(repo):
    run_cli(repo, "init")
    (repo / "docs").mkdir()
    (repo / "docs" / "README.md").write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", "docs"], check=True)
    with (repo / ".gitattributes").open("a") as f:
        f.write("docs/R*.md merge=ours\n")
    _declare(repo, "docs/*.md")
    SF.sync_attributes(repo)
    assert _merge_attr(repo, "docs/README.md") == "ours", _attributes(repo)
    assert _merge_attr(repo, "docs/new.md") == "union"


def test_a_quoted_narrower_rule_with_a_space_still_wins(repo):
    run_cli(repo, "init")
    (repo / "docs").mkdir()
    (repo / "docs" / "My File.md").write_text("x\n")
    (repo / "docs" / "guide.md").write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", "docs"], check=True)
    with (repo / ".gitattributes").open("a") as f:
        f.write('"docs/My File.md" merge=ours\n')
    _declare(repo, "docs/*.md")
    SF.sync_attributes(repo)
    assert _merge_attr(repo, "docs/My File.md") == "ours", _attributes(repo)
    assert _merge_attr(repo, "docs/guide.md") == "union"
