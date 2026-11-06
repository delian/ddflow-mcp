"""Quota/exhaustion mark + failover shed (bug B8efd3d1bc4).

A cross-family reviewer that hits its OWN quota wall -- kimi's 5-hour limit (HTTP 403),
a gemini key 403, a vendor rate-limit -- used to leave every rubber_duck/critic gate
silently `unavailable` until a human edited reviewers.toml. The fix classifies that
failure, marks the reviewer degraded (a `reviewer.degraded` event), sheds it from the
gate's pool so the next enabled cross-family reviewer serves, and surfaces the mark
instead of hiding it.
"""

from __future__ import annotations

import stat
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git

import ddflow.api.review as api
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services.review import active_degraded_marks, classify_exhaustion, degraded_marks


def _prompt_logger(tmp_path: Path, name: str, stdout: str, exit_code: int) -> Path:
    """A kind=command reviewer that appends every prompt it is sent to ``<name>.seen`` and
    then prints ``stdout`` and exits ``exit_code`` -- so a review counts HOW MANY times
    each reviewer was invoked, which is what "shed" is measured by."""
    seen = tmp_path / f"{name}.seen"
    seen.write_text("")
    cli = tmp_path / f"{name}.sh"
    cli.write_text(
        "#!/bin/sh\nin=$(cat)\n"
        f'printf "%s\\n----\\n" "$in" >> "{seen}"\n'
        f'printf "%s\\n" "{stdout}"\n'
        f"exit {exit_code}\n"
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    return cli


def _config(exhausted: Path, healthy: Path, *, cooldown_s: int = 3600) -> str:
    return (
        "[worktree]\nenabled = false\n"
        f"[review]\ndegraded_cooldown_s = {cooldown_s}\n"
        f'[[reviewer]]\nname = "exhausted"\nkind = "command"\ncommand = "{exhausted}"\n'
        'model = "kimi-for-coding-highspeed"\nfamily = "moonshot"\nhedge = 1\n'
        'gates = ["critic", "rubber_duck"]\n'
        f'[[reviewer]]\nname = "healthy"\nkind = "command"\ncommand = "{healthy}"\n'
        'model = "gemini-2.5-pro"\nfamily = "google"\nhedge = 1\n'
        'gates = ["critic", "rubber_duck"]\n'
    )


def _setup(repo: Path, tmp_path: Path, *, cooldown_s: int = 3600) -> tuple[Path, Path]:
    exhausted = _prompt_logger(tmp_path, "exhausted", "HTTP 403: rate limit exceeded", 1)
    healthy = _prompt_logger(tmp_path, "healthy", "STATUS: NO FINDINGS", 0)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(_config(exhausted, healthy, cooldown_s=cooldown_s))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add x", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "x.py").write_text("x = 1\n")
    _git(repo, "add", "x.py")
    _git(repo, "commit", "-qm", "work")
    return exhausted, healthy


def test_classify_exhaustion_matches_quota_walls_only():
    assert classify_exhaustion("HTTP 403: rate limit exceeded") == "HTTP 403"
    assert classify_exhaustion("HTTP 429: too many requests") == "HTTP 429"
    assert classify_exhaustion("HTTP/1.1 403 Forbidden") == "HTTP/1.1 403"
    assert (
        classify_exhaustion("Client error '403 Forbidden' for url 'https://api.example'") == "403"
    )
    assert classify_exhaustion("gemini key 403") == "403"
    assert classify_exhaustion("command exited 1: quota exceeded") == "quota"
    assert classify_exhaustion("insufficient_quota") == "quota"
    assert classify_exhaustion("rate_limit_exceeded") == "rate_limit"
    assert classify_exhaustion("command exited 1: your rate limit is reached") == "rate limit"
    assert classify_exhaustion("command exited 1: request limit exceeded") == "request limit"
    assert classify_exhaustion("rate limits exceeded") == "rate limits"
    # The diff's shape, not the endpoint's account: NEVER shed for these.
    assert classify_exhaustion("TRUNCATED: the model consumed all 16000 tokens") == ""
    assert classify_exhaustion("max_tokens limit exceeded: output truncated") == ""
    assert classify_exhaustion("usage limited by max_tokens/context") == ""
    assert classify_exhaustion("quotation") == ""
    assert classify_exhaustion("no candidate returned") == ""
    assert classify_exhaustion("") == ""


def test_an_exhausted_reviewer_is_marked_shed_and_the_healthy_one_serves(repo, tmp_path):
    _setup(repo, tmp_path)

    out = api.review(repo, gate="rubber_duck", item="T1", branch="feat")

    # The healthy cross-family reviewer served -- the gate is NOT unavailable.
    assert out.data["outcome"] == "passed", out.data["text"]
    assert "marked degraded" in out.data["text"], out.data["text"]
    st = fold(EventLog(repo).read_all(), strict=False)
    marks = st.items["T1"].gates["rubber_duck"].evidence.get("review_degraded")
    assert [m["reviewer"] for m in marks] == ["exhausted"], marks
    assert marks[0]["reason"] == "HTTP 403", marks
    assert marks[0]["gate"] == "rubber_duck", marks
    assert marks[0]["model"] == "kimi-for-coding-highspeed", marks
    assert marks[0]["family"] == "moonshot", marks
    # The mark is stamped with the epoch second it was written, so it can expire.
    assert isinstance(marks[0]["at"], float) and marks[0]["at"] > 0, marks

    # A later gate run sheds the degraded reviewer: the healthy one serves, the exhausted
    # one is not invoked again (its prompt log grew by nothing).
    before = (tmp_path / "exhausted.seen").read_text()
    out2 = api.review(repo, gate="critic", item="T1", branch="feat")
    assert out2.data["outcome"] == "passed", out2.data["text"]
    after = (tmp_path / "exhausted.seen").read_text()
    assert before == after, "the degraded reviewer was re-run instead of being shed"
    assert "shed exhausted" in out2.data["text"], out2.data["text"]


def test_a_doctor_note_names_the_degraded_reviewer(repo, tmp_path):
    _setup(repo, tmp_path)
    api.review(repo, gate="rubber_duck", item="T1", branch="feat")

    from ddflow.services.review import degraded_notes

    st = fold(EventLog(repo).read_all(), strict=False)
    notes = degraded_notes(st, cooldown_s=3600)
    assert notes, "the degraded mark must produce a doctor note"
    assert any("'exhausted' is degraded" in n for n in notes), notes


def _marked_state(mark_at: float) -> object:
    """A fold state whose only item recorded one reviewer gone degraded at `mark_at`.

    ``mark_at`` None writes a mark with NO timestamp (what a pre-fix log holds)."""

    row = {
        "reviewer": "exhausted",
        "reason": "HTTP 403",
        "gate": "critic",
        "model": "kimi-for-coding-highspeed",
        "family": "moonshot",
    }
    if mark_at is not None:
        row["at"] = mark_at

    class _Run:
        evidence = {"review_degraded": [row]}

    class _Item:
        gate_history = [_Run()]

    class _State:
        items = {"T1": _Item()}

    return _State()


def test_a_degraded_mark_expires_after_the_cooldown(repo, tmp_path):
    # A reviewer marked degraded at t0 is shed only while its mark is younger than the
    # cooldown. Once the cooldown has passed -- its rate window is presumed reset -- the
    # reviewer is re-eligible and served again rather than shed forever (bug B066719c493).
    st = _marked_state(mark_at=1000.0)
    assert "exhausted" in active_degraded_marks(st, cooldown_s=3600, now=1000.0)
    assert "exhausted" in active_degraded_marks(st, cooldown_s=3600, now=4599.0)
    assert "exhausted" not in active_degraded_marks(st, cooldown_s=3600, now=4600.0)
    # A mark with no timestamp (written before the fix) is treated as expired, so an
    # already-degraded reviewer is re-probed too, not shed forever for lack of one.
    assert "exhausted" not in active_degraded_marks(_marked_state(None), cooldown_s=3600, now=9_999_999.0)


def test_an_expired_degraded_reviewer_is_reprobed_not_shed_forever(repo, tmp_path):
    # With the cooldown passed (0 = re-probe every review), the previously degraded
    # reviewer is invoked again on the next gate, instead of being shed forever.
    _setup(repo, tmp_path, cooldown_s=0)
    out = api.review(repo, gate="rubber_duck", item="T1", branch="feat")
    assert out.data["outcome"] == "passed", out.data["text"]
    assert "marked degraded" in out.data["text"], out.data["text"]

    before = (tmp_path / "exhausted.seen").read_text()
    out2 = api.review(repo, gate="critic", item="T1", branch="feat")
    assert out2.data["outcome"] == "passed", out2.data["text"]
    after = (tmp_path / "exhausted.seen").read_text()
    assert after.count("----") == before.count("----") + 1, (
        "the expired reviewer was not re-probed: it is still shed forever" + "\n" + after
    )
    # It re-hit the wall on the re-probe and was marked degraded again, so it stays shed
    # for the NEXT gate (the mark is fresh), not dropped permanently. The re-mark is a NEW
    # review_degraded row strictly newer than the first run's mark: a run that never
    # re-probes, or re-probes but drops the re-mark, leaves no such newer row and fails.
    st = fold(EventLog(repo).read_all(), strict=False)
    first = st.items["T1"].gates["rubber_duck"].evidence["review_degraded"]
    second = st.items["T1"].gates["critic"].evidence.get("review_degraded")
    assert first and first[-1]["reviewer"] == "exhausted", first
    assert second and second[-1]["reviewer"] == "exhausted", second
    assert second[-1]["at"] > first[-1]["at"], (first, second)
