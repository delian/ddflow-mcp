"""A re-review sends the item's whole diff with the previous findings and their triage
(decision D-gate-economy 3, task B-gate-econ-rereview).

A delta that showed the reviewer only the follow-up commit kept reporting the fix as
absent: the code it fixed was not in what it saw. Now a plain `ddflow review` of a gate
that already has a recorded review is a FULL round over the item's diff against its base,
and the prompt lists each earlier finding with the author's verdict (confirmed: check the
fix; refuted: check the probe; untriaged), asking the reviewer to re-report one only if it
still holds and then to look for new issues. `--delta` (or `review.delta_default = true`)
still asks for a delta, for a very large diff.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import append_config, run_cli
from helpers import git as _git

import ddflow.api.review as api
from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def _setup(repo: Path, tmp_path: Path) -> Path:
    """A command reviewer that keeps every prompt it is sent and flags `FINDME` lines."""
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
        'model = "gemini-2.5-pro"\ngates = ["critic", "rubber_duck"]\n'
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "x.py").write_text("x = 1  # FINDME\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")
    return prompts


def _review(repo: Path, **kw):
    return api.review(repo, gate="critic", item="T1", branch="feat", **kw)


def _fix(repo: Path) -> None:
    (repo / "fix.py").write_text("ok = 1\n")
    _git(repo, "add", "fix.py")
    _git(repo, "commit", "-qm", "fix")


def _prompts_of(prompts: Path, call) -> str:
    """Every prompt the reviewer was sent during ``call()``, joined: one per chunk, and
    chunks may run at once, so each lands in its own file."""
    before = set(prompts.iterdir())
    call()
    return "\n".join(p.read_text() for p in sorted(set(prompts.iterdir()) - before))


def _ev(repo: Path) -> dict:
    return fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["critic"].evidence


def test_a_first_review_carries_no_previous_findings(repo, tmp_path):
    prompts = _setup(repo, tmp_path)
    assert "Previous findings" not in _prompts_of(prompts, lambda: _review(repo))


def test_a_re_review_is_a_full_round_over_the_whole_diff_with_the_triaged_findings(repo, tmp_path):
    prompts = _setup(repo, tmp_path)
    _review(repo)
    run_cli(
        repo, "review", "triage", "T1", "--gate", "critic", "--finding", "1",
        "--refuted", "--probe", "x = 1 is the intended value; the suite pins it",
    )  # fmt: skip
    _fix(repo)

    got: dict = {}
    prompt = _prompts_of(prompts, lambda: got.setdefault("out", _review(repo)))
    out = got["out"]

    assert "delta review" not in out.data["text"], out.data["text"]
    assert "re-review: the whole diff" in out.data["text"], out.data["text"]
    assert _ev(repo)["review_kind"] == "full"
    # The WHOLE diff: the original change, not only the follow-up commit.
    assert "x = 1  # FINDME" in prompt and "ok = 1" in prompt
    assert "Previous findings" in prompt
    assert "bad line" in prompt
    assert "triage: refuted -- probe: x = 1 is the intended value" in prompt
    assert "look for NEW issues" in prompt


def test_an_untriaged_finding_is_listed_as_untriaged(repo, tmp_path):
    prompts = _setup(repo, tmp_path)
    _review(repo)
    _fix(repo)
    assert "triage: untriaged" in _prompts_of(prompts, lambda: _review(repo))


def test_an_explicit_delta_is_still_only_the_new_commits(repo, tmp_path):
    prompts = _setup(repo, tmp_path)
    _review(repo)
    _fix(repo)
    got: dict = {}
    prompt = _prompts_of(prompts, lambda: got.setdefault("out", _review(repo, delta=True)))
    out = got["out"]
    assert "delta review of 1 commit since" in out.data["text"], out.data["text"]
    assert "ok = 1" in prompt and "x = 1  # FINDME" not in prompt
    # B64ed3cae18: the delta still carries the earlier findings and their triage, or the
    # reviewer contradicts a fix it cannot see; the whole diff stays out.
    assert "Previous findings" in prompt and "bad line" in prompt
    assert (
        "triage: untriaged" in prompt
        and "only the commits since" in prompt
        and "cannot be verified from a delta" in prompt
    )


def test_a_delta_prompt_tells_the_reviewer_the_delta_not_the_whole_diff(repo, tmp_path):
    """Ba32fcedea6: the delta prompt body told the reviewer to 'look for NEW issues in the
    whole diff' while showing only the delta; it must say the delta like the progress line."""
    prompts = _setup(repo, tmp_path)
    _review(repo)  # full round 1: a FINDME finding
    _fix(repo)
    prompt = _prompts_of(prompts, lambda: _review(repo, delta=True))
    assert "the commits shown" in prompt and "the whole diff" not in prompt, prompt


def test_the_cli_help_and_the_tool_schema_say_a_plain_re_review_is_full(repo):
    """They described the old default (a delta) after it flipped (roborev on 4d583a8)."""
    from ddflow.surfaces.tools import TOOLS

    _code, out, _err = run_cli(repo, "review", "--help")
    flat = " ".join(out.split())
    assert "A plain re-review is a full round with the previous findings" in flat, flat
    assert "this is the default once" not in flat
    assert "else a delta" not in TOOLS["ddflow_review"]["properties"]["full"][1]


def _confirm(repo: Path) -> None:
    """Finding #1 of T1.critic confirmed fixed, with the fix a NEW file -- not the cited
    x.py:1 line, so the finding stays confirmed instead of being superseded."""
    run_cli(
        repo, "review", "triage", "T1", "--gate", "critic", "--finding", "1",
        "--confirmed", "--probe", "added fix.py to guard the value",
    )  # fmt: skip
    (repo / "fix.py").write_text("ok = 1\n")
    _git(repo, "add", "fix.py")
    _git(repo, "commit", "-qm", "fix")


def test_a_confirmed_finding_cleared_by_a_delta_is_not_re_sent_in_a_later_delta(repo, tmp_path):
    """B2e1f4aedb5: once a delta re-checked a confirmed fix, a later delta must not re-send
    the finding -- its fix sits before that delta's base, so the delta cannot see it and the
    reviewer reports it again as unfixed."""
    prompts = _setup(repo, tmp_path)
    append_config(repo, "[review]\nmax_rounds = 0\n")

    _review(repo)  # round 1 (full): finding #1 "bad line"
    _confirm(repo)
    # Round 2 (delta) shows the fix, so the still-unrechecked confirmed finding is sent...
    first_delta = _prompts_of(prompts, lambda: _review(repo, delta=True))
    assert "bad line" in first_delta
    assert "triage: confirmed" in first_delta

    # ...and does not raise it again: the finding is cleared. A second delta's base is now
    # past the fix, so it cannot see the fix and must not send the finding at all.
    (repo / "more.py").write_text("more = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "more")
    second_delta = _prompts_of(prompts, lambda: _review(repo, delta=True))
    assert "bad line" not in second_delta
    assert "Previous findings" not in second_delta
