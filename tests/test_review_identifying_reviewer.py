"""A clean cross-family review is the identifying pass even when a same-family reviewer
also ran clean (bug Be2bb7ea786).

A gate can dispatch several reviewers -- this project mixes a same-family one with
cross-family critics for load. When both come back with no findings, the gate used to
record whichever reviewer was listed first as the identifying pass, so a clean
cross-family review was discarded and `complete` refused on the same-family one even
though a valid cross-family round had run."""

from __future__ import annotations

import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git

import ddflow.api.review as api
from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import gates as G


def _reviewer(path: Path) -> str:
    path.write_text("#!/bin/sh\ncat > /dev/null\necho 'STATUS: NO FINDINGS'\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _setup(repo: Path, tmp_path: Path) -> None:
    """A project whose critic goes to a same-family reviewer (deepseek) FIRST and a
    cross-family one (google) second, with the author a deepseek family. Both review
    clean; the identifying record must be the cross-family one."""
    same = _reviewer(tmp_path / "same")
    cross = _reviewer(tmp_path / "cross")
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        '[agent]\nfamily = "deepseek"\n'
        f'[[reviewer]]\nname = "same"\nkind = "command"\ncommand = "{same}"\n'
        'model = "deepseek-chat"\nfamily = "deepseek"\ngates = ["critic"]\n'
        f'[[reviewer]]\nname = "cross"\nkind = "command"\ncommand = "{cross}"\n'
        'model = "gemini-2.5-pro"\nfamily = "google"\ngates = ["critic"]\n'
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "x.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")


def _ev(repo: Path) -> dict:
    return fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["critic"].evidence


def test_a_clean_cross_family_review_is_the_identifying_pass(repo, tmp_path):
    _setup(repo, tmp_path)
    out = api.review(repo, gate="critic", item="T1", branch="feat")
    assert out.exit == 0, out.reason
    ev = _ev(repo)
    assert ev["reviewer"] == "cross", ev
    assert ev["model"] == "gemini-2.5-pro" and ev["family"] == "google", ev


def test_independence_is_satisfied_by_the_identifying_cross_family_reviewer(repo, tmp_path):
    _setup(repo, tmp_path)
    out = api.review(repo, gate="critic", item="T1", branch="feat")
    assert out.exit == 0, out.reason
    st = fold(EventLog(repo).read_all(), strict=False)
    cfg = Config.load(repo, env={})
    ok, why = G.reviewer_independence(st, cfg, "T1", "deepseek")
    assert ok, why


def _setup_unknown_author(repo: Path, tmp_path: Path) -> None:
    """The author has NO session model (and no `[agent].family`), so its family is
    unknown, and the critic gate goes to TWO cross-family reviewers: grok (xai) and
    gemini (google). Both review clean (bug B1a30dc3303)."""
    xai = _reviewer(tmp_path / "rxai")
    google = _reviewer(tmp_path / "rgoogle")
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        f'[[reviewer]]\nname = "grok"\nkind = "command"\ncommand = "{xai}"\n'
        'model = "grok-4.7"\nfamily = "xai"\ngates = ["critic"]\n'
        f'[[reviewer]]\nname = "gemini"\nkind = "command"\ncommand = "{google}"\n'
        'model = "gemini-2.5-pro"\nfamily = "google"\ngates = ["critic"]\n'
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "x.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")


def test_an_unknown_author_accepts_a_clean_cross_family_panel(repo, tmp_path):
    _setup_unknown_author(repo, tmp_path)
    out = api.review(repo, gate="critic", item="T1", branch="feat")
    assert out.exit == 0, out.reason
    ev = _ev(repo)
    assert ev["family"] in ("xai", "google"), ev
    st = fold(EventLog(repo).read_all(), strict=False)
    cfg = Config.load(repo, env={})
    ok, why = G.reviewer_independence(st, cfg, "T1", "")
    assert ok, why


def _setup_unknown_author_same_family_first(repo: Path, tmp_path: Path) -> None:
    """The author has NO session model (and no `[agent].family`), so its family is
    unknown at review time. A same-family reviewer (deepseek) is listed FIRST, then two
    cross-family ones -- grok (xai) and gemini (google); all review clean. The
    identifying pass must be one of the cross-family reviewers, not the same-family one
    (bug B2e5ca93520)."""
    same = _reviewer(tmp_path / "rsame")
    xai = _reviewer(tmp_path / "rxai")
    google = _reviewer(tmp_path / "rgoogle")
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        f'[[reviewer]]\nname = "same"\nkind = "command"\ncommand = "{same}"\n'
        'model = "deepseek-chat"\nfamily = "deepseek"\ngates = ["critic"]\n'
        f'[[reviewer]]\nname = "grok"\nkind = "command"\ncommand = "{xai}"\n'
        'model = "grok-4.7"\nfamily = "xai"\ngates = ["critic"]\n'
        f'[[reviewer]]\nname = "gemini"\nkind = "command"\ncommand = "{google}"\n'
        'model = "gemini-2.5-pro"\nfamily = "google"\ngates = ["critic"]\n'
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "x.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")


def test_an_unknown_author_names_a_cross_family_identifying_reviewer(repo, tmp_path):
    """A clean cross-family review is the identifying pass even when the author cannot be
    named and a same-family reviewer is listed FIRST (bug B2e5ca93520)."""
    _setup_unknown_author_same_family_first(repo, tmp_path)
    out = api.review(repo, gate="critic", item="T1", branch="feat")
    assert out.exit == 0, out.reason
    ev = _ev(repo)
    assert ev["family"] in ("xai", "google"), ev
    st = fold(EventLog(repo).read_all(), strict=False)
    cfg = Config.load(repo, env={})
    ok, why = G.reviewer_independence(st, cfg, "T1", "deepseek-ai/DeepSeek-V4-Pro-0813-Godzila")
    assert ok, why


def _reviewer_with_finding(path: Path, severity: str, where: str) -> str:
    path.write_text(
        "#!/bin/sh\ncat > /dev/null\n"
        f"echo 'FINDING {severity} {where}\nA finding from a secondary reviewer.\nSTATUS: FINDINGS 1'\n"
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _setup_clean_primary_two_finding_secondary(repo: Path, tmp_path: Path) -> None:
    """The clean reviewer is listed first, so it is the identifying pass; two secondary
    reviewers raise DISTINCT findings. The record keeps every heard reviewer's finding, so
    the review's RETURNED findings must carry BOTH -- returning only the one the gate
    names dropped the other (bug B-review-drops-a-reviewers-finding)."""
    clean = _reviewer(tmp_path / "rclean")
    med = _reviewer_with_finding(tmp_path / "rmed", "MEDIUM", "x.py:1")
    high = _reviewer_with_finding(tmp_path / "rhigh", "HIGH", "y.py:1")
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        f'[[reviewer]]\nname = "clean"\nkind = "command"\ncommand = "{clean}"\n'
        'model = "gemini-2.5-pro"\nfamily = "google"\ngates = ["critic"]\n'
        f'[[reviewer]]\nname = "med"\nkind = "command"\ncommand = "{med}"\n'
        'model = "grok-4.7"\nfamily = "xai"\ngates = ["critic"]\n'
        f'[[reviewer]]\nname = "high"\nkind = "command"\ncommand = "{high}"\n'
        'model = "grok-4.7"\nfamily = "xai"\ngates = ["critic"]\n'
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "x.py").write_text("x = 1\n")
    (repo / "y.py").write_text("y = 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")


def test_a_secondary_reviewers_finding_is_in_the_review_result(repo, tmp_path):
    """The review the caller gets back must report every heard reviewer's finding, not only
    the one the gate names: two secondary reviewers raising two distinct findings must both
    come back, so neither vanishes (bug B-review-drops-a-reviewers-finding)."""
    _setup_clean_primary_two_finding_secondary(repo, tmp_path)
    out = api.review(repo, gate="critic", item="T1", branch="feat")
    assert out.data["outcome"] == "failed", out.data
    rows = out.data["findings"]
    assert len(rows) == 2, rows
    assert any(row["severity"] == "MEDIUM" for row in rows), rows
    assert any(row["severity"] == "HIGH" for row in rows), rows
