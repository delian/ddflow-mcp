"""B00e6b5a540: a delta whose last chunk timed out recorded the gate PARTIAL over the earlier
passed round, and `--chunk` could not repair it: it diffed the item's WHOLE change, not the
delta the record covers, so its digest never matched ('the diff changed')."""

from __future__ import annotations

import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git

import ddflow.api.review as api
from ddflow.core.model import fold
from ddflow.infra.log import EventLog

OK, REFUSED = 0, 3


def _setup(repo: Path, tmp_path: Path, base: dict[str, str] | None = None) -> Path:
    """A command reviewer with no verdict for a chunk carrying BADCHUNK while `flaky`
    exists; deltas on; the first commit on branch `feat` is reviewed clean."""
    flaky = tmp_path / "flaky"
    flaky.touch()
    cli = tmp_path / "fake-reviewer"
    cli.write_text(
        "#!/bin/sh\nin=$(cat)\n"
        f"case \"$in\" in *BADCHUNK*) [ -e '{flaky}' ] && {{ echo thinking; exit 0; }};; esac\n"
        "case \"$in\" in *FINDME*) printf 'FINDING HIGH x.py:1\\nbad\\n\\nSTATUS: FINDINGS 1\\n';; "
        "*) echo 'STATUS: NO FINDINGS';; esac\n"
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic"]\nhedge = 1\nmax_chunk_chars = 150\n'
        "[review]\ndelta_default = true\nmax_rounds = 0\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    _commit(repo, base or {"base.py": "x = 0"})
    return flaky


def _commit(repo: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (repo / name).write_text(text + " " + "p" * 60 + "\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")


def _review(repo: Path, **kw):
    return api.review(repo, gate="critic", item="T1", branch="feat", **kw)


def _gate(repo: Path):
    return fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["critic"]


def test_a_partial_delta_can_be_completed_with_chunk_and_the_gate_passes(repo, tmp_path):
    flaky = _setup(repo, tmp_path)
    assert _review(repo).data["outcome"] == "passed"  # the full round
    _commit(repo, {"a.py": "x = 1", "b.py": "y = 2", "c.py": "z = 3  # BADCHUNK"})
    delta = _review(repo)
    assert delta.data["outcome"] == "partial", delta.reason
    ev = _gate(repo).evidence
    assert ev["review_kind"] == "delta" and ev["unreviewed"], ev
    (bad,) = (g["chunk"] for g in ev["unreviewed"])
    flaky.unlink()  # the reviewer answers now
    out = _review(repo, chunks=[bad])
    assert out.exit == OK and out.data["outcome"] == "passed", out.reason
    assert _gate(repo).outcome == "passed" and _gate(repo).evidence["unreviewed"] == []


def test_a_chunk_of_a_partial_delta_is_refused_once_the_diff_moved_again(repo, tmp_path):
    flaky = _setup(repo, tmp_path)
    _review(repo)
    _commit(repo, {"a.py": "x = 1", "b.py": "y = 2", "c.py": "z = 3  # BADCHUNK"})
    _review(repo)
    (bad,) = (g["chunk"] for g in _gate(repo).evidence["unreviewed"])
    flaky.unlink()
    _commit(repo, {"d.py": "w = 4"})
    out = _review(repo, chunks=[bad])
    assert out.exit == REFUSED and "diff changed" in out.reason, out.reason


def test_a_chunk_re_run_keeps_the_findings_the_delta_carried_from_the_full_round(repo, tmp_path):
    _setup(repo, tmp_path, base={"a0.py": "q = 0", "x0.py": "x = 0  # FINDME"})
    assert _review(repo).data["outcome"] == "failed"  # the full round's finding
    (carried,) = _gate(repo).evidence["chunk_findings"]
    assert carried["chunk"] >= 2, "the finding is numbered in the FULL round's cut"
    _commit(repo, {"a.py": "x = 1", "b.py": "y = 2", "c.py": "z = 3  # BADCHUNK"})
    assert _review(repo).data["outcome"] == "partial"
    out = _review(
        repo, chunks=[carried["chunk"]]
    )  # the same number in the DELTA's cut: another file
    assert out.data["outcome"] == "partial", out.reason  # the flaky chunk is still lost
    assert [f["digest"] for f in _gate(repo).evidence["chunk_findings"]] == [carried["digest"]]
    # and the record still says where its cut starts, so the next --chunk works too
    assert _gate(repo).evidence["delta_from"]


def test_a_delta_cut_from_an_explicit_base_is_completed_with_that_base(repo, tmp_path):
    flaky = _setup(repo, tmp_path)
    assert _review(repo).data["outcome"] == "passed"
    reviewed = _git(repo, "rev-parse", "HEAD")
    _commit(repo, {"a.py": "x = 1", "b.py": "y = 2", "c.py": "z = 3  # BADCHUNK"})
    assert _review(repo, base=reviewed).data["outcome"] == "partial"
    (bad,) = (g["chunk"] for g in _gate(repo).evidence["unreviewed"])
    flaky.unlink()
    out = _review(repo, chunks=[bad], base=reviewed)
    assert out.exit == OK and out.data["outcome"] == "passed", out.reason


def test_a_delta_that_cannot_be_cut_says_so(repo, tmp_path, monkeypatch):
    flaky = _setup(repo, tmp_path)
    _review(repo)
    _commit(repo, {"a.py": "x = 1", "b.py": "y = 2", "c.py": "z = 3  # BADCHUNK"})
    assert _review(repo).data["outcome"] == "partial"
    flaky.unlink()

    def broken(*_a, **_k):
        raise RuntimeError("git is unwell")

    monkeypatch.setattr(api, "_delta_diff", broken)
    out = _review(repo, chunks=[1])
    assert out.exit == REFUSED and "could not be produced (git is unwell)" in out.reason, out.reason
