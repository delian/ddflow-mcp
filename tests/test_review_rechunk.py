"""`ddflow review --chunk N`: re-review one chunk and merge it into the recorded review.

Bug Bd2332f8f2a (home-simulator 34.8f): one chunk of nine truncated deterministically in
three 25-40 minute runs. With no way to re-run that chunk alone, the agent called the
model out of band and recorded the gate passed from a call ddflow could not verify.
A re-review of chunk N merges into the record of the SAME cut (diff, chunk size,
reviewer) or is refused before any reviewer is called.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

import ddflow.api.review as api
from ddflow.core.model import fold
from ddflow.infra.log import EventLog

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _setup(repo: Path, tmp_path: Path, files: dict[str, str]) -> Path:
    """A command reviewer: no verdict for a chunk carrying BADCHUNK while the file
    `flaky` exists, a finding for FINDME, else clean. Every prompt is logged."""
    seen = tmp_path / "seen.txt"
    flaky = tmp_path / "flaky"
    flaky.touch()
    cli = tmp_path / "fake-reviewer"
    cli.write_text(
        "#!/bin/sh\nin=$(cat)\n"
        f"printf '%s\\n=====\\n' \"$in\" >> '{seen}'\n"
        f"case \"$in\" in *BADCHUNK*) [ -e '{flaky}' ] && {{ echo thinking; exit 0; }};; esac\n"
        "case \"$in\" in *FINDME*) printf 'FINDING HIGH x.py:1\\nbad\\n\\nSTATUS: FINDINGS 1\\n';; "
        "*) echo 'STATUS: NO FINDINGS';; esac\n"
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    _config(repo, cli, 150)
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    for name, text in files.items():
        (repo / name).write_text(text + " " + "p" * 60 + "\n")
    return tmp_path


def _config(repo: Path, cli: Path, chunk_chars: int) -> None:
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic"]\nhedge = 1\n'
        f"max_chunk_chars = {chunk_chars}\n"
    )


def _gate(repo: Path):
    return fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["critic"]


THREE = {"a.py": "x = 1", "b.py": "y = 2  # BADCHUNK", "c.py": "z = 3"}


def _bad_chunk(repo: Path) -> int:
    (gap,) = _gate(repo).evidence["unreviewed"]
    return gap["chunk"]


def test_a_rerun_chunk_completes_the_recorded_review(repo, tmp_path):
    tmp = _setup(repo, tmp_path, THREE)
    first = api.review(repo, gate="critic", item="T1")
    assert first.data["outcome"] == "partial", first.reason
    bad = _bad_chunk(repo)
    total = _gate(repo).evidence["chunks_total"]
    (tmp / "flaky").unlink()
    before = (tmp / "seen.txt").read_text().count("=====")
    out = api.review(repo, gate="critic", item="T1", chunks=[bad])
    assert out.exit == OK and out.data["outcome"] == "passed", out.reason
    assert (tmp / "seen.txt").read_text().count("=====") - before == 1, "only chunk N is sent"
    ev = _gate(repo).evidence
    assert _gate(repo).outcome == "passed"
    assert ev["reviewed"] == list(range(1, total + 1)) and ev["unreviewed"] == []
    assert ev["coverage"].startswith(f"{total}/{total}")


def test_findings_of_the_chunks_not_rerun_are_kept(repo, tmp_path):
    tmp = _setup(repo, tmp_path, {**THREE, "a.py": "x = 1  # FINDME"})
    api.review(repo, gate="critic", item="T1")
    (tmp / "flaky").unlink()
    out = api.review(repo, gate="critic", item="T1", chunks=[_bad_chunk(repo)])
    assert out.data["outcome"] == "failed", "the earlier chunk's finding was dropped"
    assert [f["title"] for f in out.data["findings"]] == ["x.py:1"]
    assert len(_gate(repo).evidence["chunk_findings"]) == 1


def test_a_rerun_chunk_replaces_its_own_findings(repo, tmp_path):
    tmp = _setup(repo, tmp_path, {"a.py": "x = 1  # FINDME", "c.py": "z = 3"})
    api.review(repo, gate="critic", item="T1")
    (finding,) = _gate(repo).evidence["chunk_findings"]
    # The reviewer changes its mind about that chunk: re-run it alone.
    (tmp / "fake-reviewer").write_text("#!/bin/sh\ncat >/dev/null\necho 'STATUS: NO FINDINGS'\n")
    out = api.review(repo, gate="critic", item="T1", chunks=[finding["chunk"]])
    assert out.data["outcome"] == "passed" and out.data["findings"] == [], out.data


def test_a_changed_diff_is_refused_before_any_reviewer_runs(repo, tmp_path):
    tmp = _setup(repo, tmp_path, THREE)
    api.review(repo, gate="critic", item="T1")
    recorded = _gate(repo)
    (repo / "c.py").write_text("z = 4\n")
    before = (tmp / "seen.txt").read_text()
    out = api.review(repo, gate="critic", item="T1", chunks=[1])
    assert out.exit == REFUSED and "diff changed" in out.reason, out.reason
    assert (tmp / "seen.txt").read_text() == before, "a reviewer was called"
    assert _gate(repo) == recorded, "the record was overwritten"


def test_without_a_recorded_review_there_is_nothing_to_merge_into(repo, tmp_path):
    _setup(repo, tmp_path, THREE)
    out = api.review(repo, gate="critic", item="T1", chunks=[1])
    assert out.exit == REFUSED and "run the full review first" in out.reason, out.reason


def test_a_chunk_number_outside_the_recorded_cut_is_refused(repo, tmp_path):
    _setup(repo, tmp_path, THREE)
    api.review(repo, gate="critic", item="T1")
    total = _gate(repo).evidence["chunks_total"]
    out = api.review(repo, gate="critic", item="T1", chunks=[total + 1])
    assert out.exit == REFUSED and f"1..{total}" in out.reason, out.reason


def test_a_changed_chunk_size_is_refused(repo, tmp_path):
    tmp = _setup(repo, tmp_path, THREE)
    api.review(repo, gate="critic", item="T1")
    _config(repo, tmp / "fake-reviewer", 4000)
    out = api.review(repo, gate="critic", item="T1", chunks=[1])
    assert out.exit == REFUSED and "max_chunk_chars" in out.reason, out.reason


def test_the_cli_takes_chunk_numbers(repo, tmp_path):
    tmp = _setup(repo, tmp_path, THREE)
    run_cli(repo, "review", "T1", "--gate", "critic")
    bad = _bad_chunk(repo)
    (tmp / "flaky").unlink()
    code, out, err = run_cli(repo, "review", "T1", "--gate", "critic", "--chunk", str(bad))
    assert code == OK, (out, err)
    assert _gate(repo).outcome == "passed" and f"re-reviewing chunk(s) [{bad}]" in out
    code, _out, err = run_cli(repo, "review", "T1", "--gate", "critic", "--chunk", f"{bad},1")
    assert code == OK, err
