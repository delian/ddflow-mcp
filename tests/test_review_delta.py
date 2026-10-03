"""Delta re-reviews by default (decision D-review-budget, item 2): [review].delta_default.

A shipped default for every project: once a gate has a recorded review, `ddflow review`
reviews only the commits since the head that review covered, merges the result into the
gate's record, and `--full` forces a full round. `delta_default = false` is the old
behaviour (every review a full round). Everything here runs in a throwaway project built
by `ddflow init`: no companions, no network, a command reviewer that is a shell script.
"""

from __future__ import annotations

import json
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

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _setup(repo: Path, tmp_path: Path, review_toml: str = "") -> Path:
    """A project with a command reviewer that flags every `FINDME` line (the finding's
    text is the line's own, so two reviews of the same line are byte-identical findings),
    and a feature branch `feat` with a large committed change."""
    seen = tmp_path / "seen.txt"
    cli = tmp_path / "fake-reviewer"
    cli.write_text(
        "#!/bin/sh\nin=$(cat)\n"
        f"printf 'call\\n' >> '{seen}'\n"
        'case "$in" in *FINDME-B*) printf \'FINDING MEDIUM y.py:1\\nsecond problem\\n\\n'
        "STATUS: FINDINGS 1\\n';; "
        "*FINDME*) printf 'FINDING HIGH x.py:1\\nbad\\n\\nSTATUS: FINDINGS 1\\n';; "
        "*) echo 'STATUS: NO FINDINGS';; esac\n"
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic", "rubber_duck"]\n' + review_toml
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "big.py").write_text("".join(f"line_{n} = {n}\n" for n in range(400)))
    (repo / "x.py").write_text("x = 1  # FINDME\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")
    return seen


def _review(repo: Path, **kw):
    return api.review(repo, gate=kw.pop("gate", "critic"), item="T1", branch="feat", **kw)


def _fix(repo: Path, name: str = "fix.py", text: str = "ok = 1\n") -> None:
    (repo / name).write_text(text)
    _git(repo, "add", name)
    _git(repo, "commit", "-qm", f"fix {name}")


def _ev(repo: Path, gate: str = "critic") -> dict:
    return fold(EventLog(repo).read_all(), strict=False).items["T1"].gates[gate].evidence


def _calls(seen: Path) -> int:
    return seen.read_text().count("call") if seen.exists() else 0


# -- the knob ----------------------------------------------------------------------------


def test_the_default_is_on_for_every_project_with_no_config_line(repo):
    run_cli(repo, "init")
    assert "delta_default" not in (repo / ".ddflow" / "config.toml").read_text()
    cfg = Config.load(repo, env={})
    assert cfg.review.delta_default is True
    assert cfg.sources["review.delta_default"] == "default"
    assert cfg.unknown_knobs == []
    out = run_cli(repo, "config", "--explain", "--filter", "review.delta_default")[1]
    assert "review.delta_default" in out and "--full" in out and "[default]" in out


def test_every_layer_changes_it(repo, tmp_path):
    _setup(repo, tmp_path)
    run_cli(repo, "config", "review.delta_default", "false")
    cfg = Config.load(repo, env={})
    assert cfg.review.delta_default is False and cfg.sources["review.delta_default"] == "file"
    run_cli(repo, "config", "review.delta_default", "true", "--local")
    cfg = Config.load(repo, env={})
    assert cfg.review.delta_default is True and cfg.sources["review.delta_default"] == "local"
    cfg = Config.load(repo, env={"DDFLOW_REVIEW_DELTA_DEFAULT": "0"})
    assert cfg.review.delta_default is False and cfg.sources["review.delta_default"] == "env"


def test_an_older_ddflow_reading_the_knob_notes_it_does_not_break():
    # the existing [review] unknown-key handling covers a tool built before this knob
    from ddflow.config import KNOB_DOCS

    doc = KNOB_DOCS["review.delta_default"]
    for private in ("ddflow-worktrees", "/home/", "bridge-cse", "w1-"):
        assert private not in doc


# -- the behaviour -----------------------------------------------------------------------


def test_after_a_first_review_a_one_commit_fix_is_reviewed_as_a_delta(repo, tmp_path):
    seen = _setup(repo, tmp_path)
    first = _review(repo)
    assert first.exit == OK, first.reason
    whole = _ev(repo)["diff_chars"]
    assert _ev(repo)["review_kind"] == "full" and _ev(repo)["rounds"] == 1
    _fix(repo)
    before = _calls(seen)
    out = _review(repo)  # no flag: the default
    assert out.exit == OK, out.reason
    assert _calls(seen) > before
    ev = _ev(repo)
    assert ev["review_kind"] == "delta", ev
    assert ev["diff_chars"] < whole / 10, "the delta is the fix, not the whole diff"
    assert ev["rounds"] == 1, "a delta is not a full round"
    assert "delta review of 1 commit since" in out.data["text"], out.data["text"]
    assert ev["delta_rounds"] == 1


def test_two_fix_commits_are_counted(repo, tmp_path):
    _setup(repo, tmp_path)
    _review(repo)
    _fix(repo, "a.py")
    _fix(repo, "b.py")
    out = _review(repo)
    assert "delta review of 2 commits since" in out.data["text"], out.data["text"]


def test_full_forces_a_full_round_that_counts(repo, tmp_path):
    _setup(repo, tmp_path)
    _review(repo)
    whole = _ev(repo)["diff_chars"]
    _fix(repo)
    out = _review(repo, full=True)
    assert out.exit == OK, out.reason
    ev = _ev(repo)
    assert ev["review_kind"] == "full" and ev["rounds"] == 2 and ev["round"] == 2
    assert ev["diff_chars"] >= whole
    assert "delta review" not in out.data["text"]
    # and it is bounded by [review].max_rounds like any full round
    _fix(repo, "c.py")
    refused = _review(repo, full=True)
    assert refused.exit == REFUSED and "max_rounds" in refused.reason


def test_full_and_delta_together_are_refused(repo, tmp_path):
    _setup(repo, tmp_path)
    out = _review(repo, full=True, delta=True)
    assert out.exit == FAIL and "--full" in out.reason and "--delta" in out.reason


def test_a_rebased_branch_falls_back_to_a_full_round_and_says_why(repo, tmp_path):
    _setup(repo, tmp_path)
    _review(repo)
    _git(repo, "commit", "-q", "--amend", "-m", "work, rewritten")  # head is no ancestor now
    out = _review(repo)
    assert out.exit == OK, out.reason
    ev = _ev(repo)
    assert ev["review_kind"] == "full" and ev["rounds"] == 2
    text = out.data["text"]
    assert "full round" in text and "not an ancestor" in text and "rebased" in text, text


def test_the_explicit_delta_flag_also_falls_back_after_a_rebase(repo, tmp_path):
    _setup(repo, tmp_path)
    _review(repo)
    _git(repo, "commit", "-q", "--amend", "-m", "rewritten")
    out = _review(repo, delta=True)
    assert out.exit == OK and _ev(repo)["review_kind"] == "full"
    assert "not an ancestor" in out.data["text"]


def test_nothing_changed_since_the_reviewed_head_says_how_to_force(repo, tmp_path):
    seen = _setup(repo, tmp_path)
    _review(repo)
    calls = _calls(seen)
    out = _review(repo)
    assert out.exit == REFUSED and "nothing changed" in out.reason and "--full" in out.reason
    assert _calls(seen) == calls


def test_delta_default_false_is_the_old_behaviour(repo, tmp_path):
    _setup(repo, tmp_path, "[review]\ndelta_default = false\nmax_rounds = 0\n")
    _review(repo)
    whole = _ev(repo)["diff_chars"]
    _fix(repo)
    out = _review(repo)
    ev = _ev(repo)
    assert out.exit == OK and ev["review_kind"] == "full" and ev["rounds"] == 2
    assert ev["diff_chars"] >= whole
    assert "delta review" not in out.data["text"]
    # an explicit --delta still works with the default off
    _fix(repo, "d.py")
    assert _review(repo, delta=True).exit == OK and _ev(repo)["review_kind"] == "delta"


def test_a_first_review_and_one_after_an_unavailable_one_are_full(repo, tmp_path):
    _setup(repo, tmp_path)
    run_cli(repo, "gate", "skip", "T1", "critic", "--reason", "x")
    out = _review(repo)
    assert out.exit == OK and _ev(repo)["review_kind"] == "full"


def test_a_partial_prior_review_is_not_deltaed_over(repo, tmp_path):
    """A delta from a head whose review skipped chunks would never cover them."""
    _setup(repo, tmp_path, "[review]\nmax_rounds = 0\n")
    _review(repo)
    log = EventLog(repo)
    ev = dict(_ev(repo), status="PARTIAL")
    log.append("gate.partial", "T1", {"gate": "critic", "outcome": "partial", "evidence": ev})
    _fix(repo)
    out = _review(repo)
    assert out.exit == OK and _ev(repo)["review_kind"] == "full", out.data["text"]
    assert "partial" in out.data["text"]


def test_a_commit_or_base_review_is_not_second_guessed(repo, tmp_path):
    _setup(repo, tmp_path)
    _review(repo)
    _fix(repo)
    sha = _git(repo, "rev-parse", "HEAD")
    out = api.review(repo, gate="critic", item="T1", commit=sha)
    assert out.exit == OK and _ev(repo)["review_kind"] == "delta"
    assert "delta review of" not in out.data["text"]


def test_the_other_gate_is_independent(repo, tmp_path):
    _setup(repo, tmp_path)
    _review(repo)
    _fix(repo)
    out = _review(repo, gate="rubber_duck")  # no rubber_duck review yet: a full round
    assert out.exit == OK and _ev(repo, "rubber_duck")["review_kind"] == "full"


# -- the merge into the gate record -----------------------------------------------------


def test_the_delta_merges_into_the_record_keeping_earlier_triage(repo, tmp_path):
    _setup(repo, tmp_path)
    _review(repo)  # finding #1: x.py FINDME
    assert (
        api.triage(repo, "T1", gate="critic", finding=1, verdict="confirmed", probe="fixed").exit
        == OK
    )
    _fix(repo, "y.py", "y = 1  # FINDME-B\n")  # a different finding
    out = _review(repo)
    assert out.exit == OK
    ev = _ev(repo)
    titles = [f["title"] for f in ev["chunk_findings"]]
    assert len(titles) == 2, ev["chunk_findings"]
    assert ev["delta_findings"] == 1
    st = fold(EventLog(repo).read_all(), strict=False)
    from ddflow.services import gates as G

    counts = G.triage_counts(st.items["T1"], "critic")
    # the earlier finding keeps its verdict (same digest); the new one is untriaged
    assert counts == {"findings": 2, "refuted": 0, "confirmed": 1, "untriaged": 1}, counts
    assert "#2" in out.data["text"], "the delta's finding says its place in the merged record"


def test_a_byte_identical_finding_is_not_duplicated_and_stays_triaged(repo, tmp_path):
    _setup(repo, tmp_path)
    _review(repo)
    api.triage(repo, "T1", gate="critic", finding=1, verdict="refuted", probe="p")
    _fix(repo, "x2.py", "x = 1  # FINDME\n")  # the reviewer repeats the same finding text
    _review(repo)
    from ddflow.services import gates as G

    st = fold(EventLog(repo).read_all(), strict=False)
    counts = G.triage_counts(st.items["T1"], "critic")
    assert counts and counts["findings"] == 1 and counts["refuted"] == 1, counts


def test_a_changed_finding_is_not_carried(repo, tmp_path):
    _setup(repo, tmp_path)
    _review(repo)
    api.triage(repo, "T1", gate="critic", finding=1, verdict="refuted", probe="p")
    _fix(repo, "y.py", "y = 1  # FINDME-B\n")
    _review(repo)
    from ddflow.services import gates as G

    st = fold(EventLog(repo).read_all(), strict=False)
    counts = G.triage_counts(st.items["T1"], "critic")
    assert counts["untriaged"] == 1 and counts["refuted"] == 1


def test_a_clean_delta_keeps_the_earlier_findings_visible(repo, tmp_path):
    _setup(repo, tmp_path)
    _review(repo)
    _fix(repo)  # the fix is clean
    out = _review(repo)
    ev = _ev(repo)
    assert out.exit == OK and ev["delta_findings"] == 0
    assert len(ev["chunk_findings"]) == 1, "the first round's finding is still on the record"
    assert ev["full_coverage"], ev


# -- gate status and surfaces ------------------------------------------------------------


def test_gate_status_shows_full_and_delta_rounds_separately(repo, tmp_path):
    _setup(repo, tmp_path)
    _review(repo)
    _fix(repo)
    _review(repo)
    _fix(repo, "g.py")
    _review(repo)
    code, out, err = run_cli(repo, "gate", "status", "T1")
    assert code == OK, err
    assert "1 full round, 2 delta rounds" in out, out


def test_the_cli_has_full_and_refuses_it_with_delta(repo, tmp_path):
    _setup(repo, tmp_path)
    code, _o, err = run_cli(repo, "review", "T1", "--gate", "critic", "--full", "--delta")
    assert code == FAIL and "--full" in err, err
    code, _o, err = run_cli(repo, "review", "T1", "--gate", "critic", "--branch", "feat")
    assert code == OK, err
    code, _o, err = run_cli(repo, "review", "T1", "--gate", "critic", "--branch", "feat", "--full")
    assert code == OK, err


def _mcp(repo, name, **args):
    from ddflow.surfaces.mcp import Server

    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        }
    )
    return json.dumps(reply)


def test_mcp_review_takes_full_and_runs_the_same_default(repo, tmp_path):
    _setup(repo, tmp_path)
    _mcp(repo, "ddflow_review", id="T1", gate="critic", branch="feat")
    _fix(repo)
    reply = _mcp(repo, "ddflow_review", id="T1", gate="critic", branch="feat")
    assert "delta review of 1 commit since" in reply, reply
    assert _ev(repo)["review_kind"] == "delta"
    _fix(repo, "e.py")
    _mcp(repo, "ddflow_review", id="T1", gate="critic", branch="feat", full=True)
    assert _ev(repo)["review_kind"] == "full"


def test_mcp_configure_sets_it_on_both_layers_and_tells_the_operator(repo, tmp_path):
    _setup(repo, tmp_path)
    shared = _mcp(repo, "ddflow_configure", set="review.delta_default", value="false")
    assert "operator" in shared.lower(), shared
    assert Config.load(repo, env={}).review.delta_default is False
    local = _mcp(repo, "ddflow_configure", set="review.delta_default", value="true", local=True)
    assert "operator" in local.lower(), local
    cfg = Config.load(repo, env={})
    assert cfg.review.delta_default is True and cfg.sources["review.delta_default"] == "local"


def test_it_holds_under_a_custom_workflow(repo, tmp_path):
    _setup(repo, tmp_path)
    code, _out, err = run_cli(repo, "workflow", "pipeline", "task", "implement,critic,merge")
    assert code == OK, err
    _review(repo)
    _fix(repo)
    _review(repo)
    assert _ev(repo)["review_kind"] == "delta"


# -- shipped with the package ------------------------------------------------------------


def test_the_shipped_docs_describe_it_and_name_no_repository():
    root = Path(__file__).resolve().parents[1]
    for rel in (
        "README.md",
        "ddflow/templates/drivers/implement-phase.md",
        "ddflow/templates/prompts/help/gates.md",
        "ddflow/templates/prompts/mcp_instructions.md",
    ):
        text = (root / rel).read_text()
        assert "delta_default" in text and ("--full" in text or "full=true" in text), rel


# -- findings of the review of this change ----------------------------------------------


def test_force_is_a_full_round_even_when_the_default_would_make_it_a_delta(repo, tmp_path):
    _setup(repo, tmp_path)
    _review(repo)
    _review(repo, full=True)  # the cap (2) is spent
    _fix(repo)
    out = _review(repo, force=True, reason="operator asked")
    assert out.exit == OK, out.reason
    ev = _ev(repo)
    assert ev["review_kind"] == "full" and ev["round"] == 3 and ev["budget_forced"]
    assert "delta review" not in out.data["text"]


def test_a_delta_that_reached_no_reviewer_does_not_move_the_reviewed_head(repo, tmp_path):
    _setup(repo, tmp_path)
    _review(repo)
    _fix(repo, "a.py")
    (tmp_path / "fake-reviewer").write_text("#!/bin/sh\nexit 1\n")  # the reviewer is down
    down = _review(repo)
    assert down.exit != OK
    assert "reviewed_head" not in _ev(repo), "no reviewer saw this head"
    (tmp_path / "fake-reviewer").write_text(
        "#!/bin/sh\ncat >/dev/null\necho 'STATUS: NO FINDINGS'\n"
    )
    _fix(repo, "b.py")
    out = _review(repo)
    assert out.exit == OK, out.reason
    assert "delta review of 2 commits since" in out.data["text"], out.data["text"]


def test_a_plain_review_after_a_gate_skip_is_still_a_delta(repo, tmp_path):
    """Probe for a review finding: the log still knows the reviewed head after a skip
    replaced the gate's record, and a delta from it is sound (the skip changed no code)."""
    _setup(repo, tmp_path)
    _review(repo)
    run_cli(repo, "gate", "skip", "T1", "critic", "--reason", "x")
    _fix(repo)
    out = _review(repo)
    assert out.exit == OK and _ev(repo)["review_kind"] == "delta", out.reason


def test_the_evidence_carries_the_resolved_round_kind_not_the_flag(repo, tmp_path):
    """Probe for a review finding: `full` is an argument, never an evidence field."""
    _setup(repo, tmp_path)
    _review(repo)
    _fix(repo)
    _review(repo, full=False)
    ev = _ev(repo)
    assert "full" not in ev and ev["review_kind"] == "delta"


def test_a_clean_delta_does_not_clear_findings_nobody_triaged(repo, tmp_path):
    """Probe for a review finding: the delta does not see an unfixed finding, so the
    record's own untriaged findings hold the gate; once triaged, the next clean delta passes."""
    _setup(repo, tmp_path)
    _review(repo)  # finding #1
    _fix(repo)
    held = _review(repo)
    assert held.data["outcome"] == "failed", held.data
    assert "no triage verdict" in held.data["text"]
    assert (
        api.triage(repo, "T1", gate="critic", finding=1, verdict="confirmed", probe="p").exit == OK
    )
    _fix(repo, "h.py")
    out = _review(repo)
    assert out.data["outcome"] == "passed", out.data


def test_force_with_delta_is_refused_not_silently_a_delta(repo, tmp_path):
    _setup(repo, tmp_path)
    _review(repo)
    out = _review(repo, force=True, reason="why", delta=True)
    assert out.exit == FAIL and "--force" in out.reason and "--delta" in out.reason
