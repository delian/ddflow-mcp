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
    assert SF.sync_attributes(repo, Config.load(repo)) == []


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
