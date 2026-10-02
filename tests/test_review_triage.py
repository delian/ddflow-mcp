"""`ddflow review triage`: what became of each finding, in the log (bug B9d8bd466c3, part 2).

Decision D-review-triage (operator 2026-10-02): a review that reports findings stays
recorded `failed` -- which does not block completion (D-failed-critic-not-blocking) -- and
the author's triage of each finding is its own event. Before it, an author who refuted
every finding re-recorded the gate `passed`, and the log read as if a fix had happened.
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

import ddflow.api.review as api
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import gates as G

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _setup(repo: Path, tmp_path: Path) -> Path:
    """A command reviewer reporting one finding for each file marked FINDA / FINDB; the
    wording of FINDA's finding is read from `wording` so a test can change it."""
    wording = tmp_path / "wording"
    wording.write_text("first wording")
    cli = tmp_path / "fake-reviewer"
    cli.write_text(
        "#!/bin/sh\nin=$(cat)\n"
        f"w=$(cat '{wording}')\n"
        'case "$in" in *FINDA*) printf "FINDING HIGH a.py:1\\n$w\\n\\n";; esac\n'
        'case "$in" in *FINDB*) printf "FINDING LOW b.py:1\\nsecond finding\\n\\n";; esac\n'
        'case "$in" in *FIND*) echo "STATUS: FINDINGS 1";; *) echo "STATUS: NO FINDINGS";; esac\n'
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic"]\nhedge = 1\nmax_chunk_chars = 150\n'
    )
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    for name, mark in (("a.py", "FINDA"), ("b.py", "FINDB"), ("c.py", "clean")):
        (repo / name).write_text(f"x = 1  # {mark} " + "p" * 60 + "\n")
    return wording


def _state(repo: Path):
    return fold(EventLog(repo).read_all(), strict=False).items["T1"]


def _triage(repo: Path, n: int, verdict: str = "refuted", probe: str = "ran it: no such bug"):
    return api.triage(repo, "T1", gate="critic", finding=n, verdict=verdict, probe=probe)


def _full_review(repo: Path):
    out = api.review(repo, gate="critic", item="T1")
    assert out.data["outcome"] == "failed", out.reason
    return out


def test_findings_are_numbered_and_carry_a_digest_of_their_text(repo, tmp_path):
    _setup(repo, tmp_path)
    lines: list[str] = []
    api.review(repo, gate="critic", item="T1", on_progress=lines.append)
    shown = [ln.strip() for ln in lines if ln.strip().startswith("#")]
    assert [s.split()[0] for s in shown] == ["#1", "#2"], lines
    found = _state(repo).gates["critic"].evidence["chunk_findings"]
    assert [f["severity"] for f in found] == ["HIGH", "LOW"]
    assert all(len(f["digest"]) == 16 for f in found) and found[0]["digest"] != found[1]["digest"]


def test_a_triage_is_its_own_event_and_the_gate_stays_failed(repo, tmp_path):
    _setup(repo, tmp_path)
    _full_review(repo)
    out = _triage(repo, 1)
    assert out.exit == OK, out.reason
    assert "1 refuted" in out.data["text"] and "1 untriaged" in out.data["text"]
    events = [e for e in EventLog(repo).read_all() if e.kind == "review.triaged"]
    assert len(events) == 1 and events[0].subject == "T1"
    assert events[0].data["verdict"] == "refuted" and events[0].data["probe"].startswith("ran it")
    it = _state(repo)
    assert it.gates["critic"].outcome == "failed", "triage must not rewrite the outcome"
    assert [e.kind for e in EventLog(repo).read_all()].count("gate.failed") == 1


def test_gate_status_and_show_say_how_the_findings_stand(repo, tmp_path):
    _setup(repo, tmp_path)
    _full_review(repo)
    _triage(repo, 1, "refuted")
    _triage(repo, 2, "confirmed", "fixed in abc123, tests/test_x.py::t")
    line = "2 finding(s): 1 refuted, 1 confirmed, 0 untriaged"
    st = fold(EventLog(repo).read_all(), strict=False)
    from ddflow.config import Config

    assert line in G.status(st, Config.load(repo), "T1").render()
    code, out, _err = run_cli(repo, "gate", "status", "T1")
    assert code == OK and f"[!] critic  -- {line}" in out, out
    code, out, _err = run_cli(repo, "show", "T1")
    assert code == OK and f"[!] critic  -- {line}" in out, out


def test_a_gate_with_untriaged_findings_says_so(repo, tmp_path):
    _setup(repo, tmp_path)
    _full_review(repo)
    _code, out, _err = run_cli(repo, "gate", "status", "T1")
    assert "2 finding(s): 0 refuted, 0 confirmed, 2 untriaged" in out, out


def test_a_triage_can_be_changed_and_the_log_keeps_both(repo, tmp_path):
    _setup(repo, tmp_path)
    _full_review(repo)
    _triage(repo, 1, "refuted")
    _triage(repo, 1, "confirmed", "after all: fixed in abc123")
    assert len([e for e in EventLog(repo).read_all() if e.kind == "review.triaged"]) == 2
    counts = G.triage_counts(_state(repo), "critic")
    assert counts == {"findings": 2, "refuted": 0, "confirmed": 1, "untriaged": 1}


def test_refusals_say_why_and_record_nothing(repo, tmp_path):
    _setup(repo, tmp_path)
    assert "no `ddflow review` with numbered findings" in _triage(repo, 1).reason
    _full_review(repo)
    cases = {
        "no finding #3": _triage(repo, 3),
        "no finding #0": _triage(repo, 0),
        "--refuted (the probe": _triage(repo, 1, verdict=""),
        "--probe is required": _triage(repo, 1, probe="  "),
    }
    for needle, out in cases.items():
        assert out.exit == FAIL and needle in out.reason, (needle, out.reason)
    assert api.triage(repo, "NOPE", finding=1, verdict="refuted", probe="p").exit == FAIL
    assert not [e for e in EventLog(repo).read_all() if e.kind == "review.triaged"]


def test_a_hand_recorded_gate_has_nothing_to_triage(repo, tmp_path):
    _setup(repo, tmp_path)
    run_cli(repo, "gate", "record", "T1", "critic", "--outcome", "failed", "--reason", "x")
    assert "numbered findings" in _triage(repo, 1).reason


def test_a_rereview_keeps_a_triage_only_for_a_finding_worded_the_same(repo, tmp_path):
    wording = _setup(repo, tmp_path)
    _full_review(repo)
    _triage(repo, 1)
    _triage(repo, 2)
    _full_review(repo)  # the same reviewer says the same things
    assert G.triage_counts(_state(repo), "critic")["refuted"] == 2, "an identical finding"
    wording.write_text("a DIFFERENT wording of finding one")
    _full_review(repo)
    counts = G.triage_counts(_state(repo), "critic")
    assert counts == {"findings": 2, "refuted": 1, "confirmed": 0, "untriaged": 1}, counts
    found = _state(repo).gates["critic"].evidence["chunk_findings"]
    assert found[0]["title"] == "a.py:1" and found[0]["detail"].startswith("a DIFFERENT")


def test_a_chunk_rerun_keeps_the_triage_of_the_findings_it_did_not_touch(repo, tmp_path):
    _setup(repo, tmp_path)
    _full_review(repo)
    _triage(repo, 1)
    _triage(repo, 2)
    second = _state(repo).gates["critic"].evidence["chunk_findings"][1]["chunk"]
    api.review(repo, gate="critic", item="T1", chunks=[second])
    assert G.triage_counts(_state(repo), "critic")["refuted"] == 2


def test_the_cli_verb_and_an_item_named_triage(repo, tmp_path):
    _setup(repo, tmp_path)
    _full_review(repo)
    code, out, err = run_cli(
        repo,
        "review",
        "triage",
        "T1",
        "--gate",
        "critic",
        "--finding",
        "1",
        "--refuted",
        "--probe",
        "python probe.py: no crash",
    )
    assert code == OK and "T1.critic finding #1 [HIGH] refuted" in out, (out, err)
    for argv in (
        ["--finding", "1", "--probe", "p"],  # no verdict
        ["--finding", "1", "--refuted", "--confirmed", "--probe", "p"],  # both
        ["--finding", "9", "--refuted", "--probe", "p"],  # out of range
        ["--finding", "1", "--refuted"],  # no probe
    ):
        code, _out, err = run_cli(repo, "review", "triage", "T1", "--gate", "critic", *argv)
        assert code == FAIL and err.strip(), (argv, err)
    code, _out, err = run_cli(repo, "review", "triage")
    assert code == FAIL and "usage" in err


def test_the_mcp_tool_records_the_same_event(repo, tmp_path):
    from ddflow.surfaces.mcp import TOOLS

    _setup(repo, tmp_path)
    _full_review(repo)
    spec = TOOLS["ddflow_review_triage"]
    out = spec["api"](
        repo,
        {"id": "T1", "gate": "critic", "finding": 2, "verdict": "confirmed", "probe": "fixed"},
        "",
    )
    assert out.exit == OK and out.data["counts"]["confirmed"] == 1
    (event,) = [e for e in EventLog(repo).read_all() if e.kind == "review.triaged"]
    assert event.data["finding"] == 2 and event.data["severity"] == "LOW"
    assert json.dumps(out.data["counts"])


def test_a_triage_event_for_an_unknown_item_folds_to_nothing():
    from ddflow.core.events import Event

    st = fold(
        [
            Event(
                id="e1",
                kind="review.triaged",
                subject="GHOST",
                data={"gate": "critic", "digest": "d", "verdict": "refuted"},
                agent="a",
                ts="t",
                lamport=1,
            )
        ],
        strict=False,
    )
    assert "GHOST" not in st.items


def test_identical_findings_of_one_review_are_triaged_one_at_a_time(repo, tmp_path):
    """critic: two word-for-word identical findings shared a digest, so one triage
    marked both and a second overwrote the first."""
    _setup(repo, tmp_path)
    (repo / "d.py").write_text("x = 1  # FINDA " + "p" * 60 + "\n")  # same finding as a.py's
    _full_review(repo)
    digests = [f["digest"] for f in _state(repo).gates["critic"].evidence["chunk_findings"]]
    assert len(digests) == 3 and len(set(digests)) == 3, digests
    _triage(repo, 1)
    assert G.triage_counts(_state(repo), "critic")["refuted"] == 1, "one event, one finding"


def test_with_two_reviewers_the_recorded_ones_findings_are_named(repo, tmp_path):
    """critic: '#1' under the second reviewer's block was the first reviewer's #1."""
    _setup(repo, tmp_path)
    cfg = repo / ".ddflow" / "config.toml"
    first = cfg.read_text().split("[[reviewer]]")[1]
    cfg.write_text(
        cfg.read_text() + "\n[[reviewer]]\n" + first.replace('name = "fake"', 'name = "fake2"')
    )
    lines: list[str] = []
    api.review(repo, gate="critic", item="T1", on_progress=lines.append)
    text = "\n".join(lines)
    assert "fake FAILED" not in text and "fake REVIEWED" in text and "fake2 REVIEWED" in text
    assert "triage addresses fake's findings: #1..#2" in text, text
