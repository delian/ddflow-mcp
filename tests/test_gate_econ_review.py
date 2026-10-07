"""One combined cross-family review for a small change (decision D-gate-economy 2, task
B-gate-econ-review-combined).

`ddflow review <id> --gate rubber_duck,critic` on a diff under
`[review].combined_under_lines` (default 150 changed lines) sends ONE request whose prompt
covers both lenses and records the outcome for both gates, with a shared `review_id` in
their evidence. A larger diff keeps both reviews, run in parallel. Measured before the
decision: a bug fix took 2.9 rounds of each gate, most failing on findings later refuted.
"""

from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

import ddflow.api.review as api
from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog

GATES = "rubber_duck,critic"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _setup(repo: Path, tmp_path: Path, review_toml: str = "", lines: int = 3) -> Path:
    """A command reviewer (gemini family) that keeps each prompt it is sent and flags
    `FINDME` lines; a branch `feat` changing ``lines`` lines."""
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    cli = tmp_path / "fake-reviewer"
    cli.write_text(
        "#!/bin/sh\nin=$(cat)\n"
        f'printf "%s" "$in" > "{prompts}/$$.txt"\n'
        'case "$in" in *FINDME*) printf \'FINDING HIGH x.py:1\\nbad line\\n\\n'
        "STATUS: FINDINGS 1\\n';; *) echo 'STATUS: NO FINDINGS';; esac\n"
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\nhedge = 1\ngates = ["critic", "rubber_duck"]\n' + review_toml
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "x.py").write_text(
        "x = 1  # FINDME\n" + "".join(f"y{n} = {n}\n" for n in range(lines - 1))
    )
    _git(repo, "add", "x.py")
    _git(repo, "commit", "-qm", "work")
    return prompts


def _gates(repo: Path) -> dict:
    return fold(EventLog(repo).read_all(), strict=False).items["T1"].gates


def test_the_knob_defaults_to_150_lines(repo):
    run_cli(repo, "init")
    assert Config.load(repo, env={}).review.combined_under_lines == 150


def test_a_small_diff_gets_one_review_recorded_for_both_gates(repo, tmp_path):
    prompts = _setup(repo, tmp_path)

    out = api.review(repo, gate=GATES, item="T1", branch="feat")

    sent = list(prompts.iterdir())
    assert len(sent) == 1, f"{len(sent)} reviewer requests for a {GATES} combined review"
    prompt = sent[0].read_text()
    assert "rubber_duck" in prompt and "critic" in prompt, "the prompt must name both lenses"
    gates = _gates(repo)
    duck, critic = gates["rubber_duck"], gates["critic"]
    assert duck.outcome == critic.outcome == "failed", (duck.outcome, critic.outcome)
    rid = duck.evidence.get("review_id")
    assert rid and rid == critic.evidence.get("review_id")
    assert duck.evidence["combined_gates"] == ["rubber_duck", "critic"]
    assert duck.evidence["chunk_findings"] == critic.evidence["chunk_findings"]
    assert duck.by == critic.by
    assert "combined review" in out.data["text"], out.data["text"]


def test_each_gate_of_a_combined_review_can_be_triaged(repo, tmp_path):
    _setup(repo, tmp_path)
    api.review(repo, gate=GATES, item="T1", branch="feat")
    for gate in ("rubber_duck", "critic"):
        code, out, err = run_cli(
            repo, "review", "triage", "T1", "--gate", gate, "--finding", "1",
            "--refuted", "--probe", "x = 1 is intended",
        )  # fmt: skip
        assert code == 0, (gate, out, err)


def test_a_large_diff_keeps_both_reviews(repo, tmp_path):
    prompts = _setup(repo, tmp_path, "[review]\ncombined_under_lines = 2\n", lines=5)

    out = api.review(repo, gate=GATES, item="T1", branch="feat")

    assert len(list(prompts.iterdir())) == 2, "a large diff gets one review per gate"
    gates = _gates(repo)
    assert gates["rubber_duck"].outcome == gates["critic"].outcome == "failed"
    assert "review_id" not in gates["critic"].evidence
    assert "rubber_duck" in out.data["text"] and "critic" in out.data["text"]


def test_zero_turns_combining_off(repo, tmp_path):
    prompts = _setup(repo, tmp_path, "[review]\ncombined_under_lines = 0\n")
    api.review(repo, gate=GATES, item="T1", branch="feat")
    assert len(list(prompts.iterdir())) == 2


def test_the_cli_takes_the_two_gates(repo, tmp_path):
    _setup(repo, tmp_path)
    code, out, err = run_cli(repo, "review", "T1", "--gate", GATES, "--branch", "feat")
    assert code == 0, (code, out, err)
    assert "recorded T1.rubber_duck = failed" in out + err, out + err
    assert "recorded T1.critic = failed" in out + err, out + err
