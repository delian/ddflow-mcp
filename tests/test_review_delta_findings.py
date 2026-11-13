"""Delta findings are numbered, merged and carried across rounds (split from test_review_delta.py)."""

from __future__ import annotations

import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git
from review_delta_support import (
    OK,
    _delta_on,
    _ev,
    _fix,
    _record,
    _review,
    _setup,
)

import ddflow.api.review as api
from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def _setup_many(repo: Path, tmp_path: Path) -> None:
    """One command reviewer that flags up to four distinct findings (markers ONE..FOUR),
    so a delta can add a finding beyond a full round's first three."""
    cli = tmp_path / "fake-reviewer"
    cli.write_text(
        "#!/bin/sh\nin=$(cat)\nn=0\n"
        "case \"$in\" in *ONE*) printf 'FINDING HIGH a.py:1\\nfirst bad thing\\n\\n'; n=$((n+1));; esac\n"
        "case \"$in\" in *TWO*) printf 'FINDING MEDIUM b.py:1\\nsecond bad thing\\n\\n'; n=$((n+1));; esac\n"
        "case \"$in\" in *THREE*) printf 'FINDING LOW c.py:1\\nthird bad thing\\n\\n'; n=$((n+1));; esac\n"
        "case \"$in\" in *FOUR*) printf 'FINDING LOW d.py:1\\nfourth bad thing\\n\\n'; n=$((n+1));; esac\n"
        "if [ \"$n\" -gt 0 ]; then printf 'STATUS: FINDINGS %s\\n' \"$n\"; else echo 'STATUS: NO FINDINGS'; fi\n"
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic"]\n' + _delta_on("")
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "a.py").write_text("x = 1  # ONE\n")
    (repo / "b.py").write_text("x = 1  # TWO\n")
    (repo / "c.py").write_text("x = 1  # THREE\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")


def test_a_delta_finding_is_numbered_by_its_place_in_the_record(repo, tmp_path):
    """B32ce00713b: the round output numbered the delta's new finding #1 while the record
    (which `review triage --finding N` addresses) held it at #4 -- so `--finding 1` marked
    an earlier finding, not the one the agent just saw in the round's output."""
    _setup_many(repo, tmp_path)
    _review(repo)  # full round: findings #1 ONE, #2 TWO, #3 THREE
    _fix(repo, "d.py", "x = 1  # FOUR\n")  # a finding the earlier round never saw
    lines: list[str] = []
    out = api.review(repo, gate="critic", item="T1", branch="feat", on_progress=lines.append)
    assert out.exit == OK, out.reason
    ev = _ev(repo)
    assert ev["review_kind"] == "delta", ev
    found = ev["chunk_findings"]
    assert len(found) == 4 and "d.py:1" in found[3]["location"], found
    # The delta's finding is #4 of the record, not #1 of the round, in the round's output.
    shown = [ln.strip() for ln in lines if ln.strip().startswith("#")]
    assert [s.split()[0] for s in shown if "d.py:1" in s] == ["#4"], shown
    # ... and triage --finding 4 marks THIS finding (d.py), while #1 still marks the first.
    triaged = api.triage(
        repo, "T1", gate="critic", finding=4, verdict="confirmed", probe="fixed d.py"
    )
    assert triaged.exit == OK, triaged.reason
    (event,) = [e for e in EventLog(repo).read_all() if e.kind == "review.triaged"]
    assert event.data["finding"] == 4 and event.data["location"] == "d.py:1", event.data


def test_two_reviewers_delta_findings_are_numbered_across_the_record(repo, tmp_path):
    """B32ce00713b: two reviewers in one delta round must not both number their new
    finding #1 -- the second's takes the next place in the record the triage addresses."""
    first = tmp_path / "rev-a"
    first.write_text(
        "#!/bin/sh\nin=$(cat)\nn=0\n"
        "case \"$in\" in *ONE*) printf 'FINDING HIGH a.py:1\\nfirst bad thing\\n\\n'; n=$((n+1));; esac\n"
        "case \"$in\" in *FOUR*) printf 'FINDING LOW d.py:1\\nfourth bad thing\\n\\n'; n=$((n+1));; esac\n"
        "if [ \"$n\" -gt 0 ]; then printf 'STATUS: FINDINGS %s\\n' \"$n\"; else echo 'STATUS: NO FINDINGS'; fi\n"
    )
    second = tmp_path / "rev-b"
    second.write_text(
        "#!/bin/sh\nin=$(cat)\nn=0\n"
        "case \"$in\" in *ONE*) printf 'FINDING MEDIUM b.py:1\\nsecond bad thing\\n\\n'; n=$((n+1));; esac\n"
        "case \"$in\" in *FOUR*) printf 'FINDING LOW e.py:1\\nfifth bad thing\\n\\n'; n=$((n+1));; esac\n"
        "if [ \"$n\" -gt 0 ]; then printf 'STATUS: FINDINGS %s\\n' \"$n\"; else echo 'STATUS: NO FINDINGS'; fi\n"
    )
    for cli in (first, second):
        cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        f'[[reviewer]]\nname = "a"\nkind = "command"\ncommand = "{first}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic"]\n'
        f'[[reviewer]]\nname = "b"\nkind = "command"\ncommand = "{second}"\n'
        'model = "gemini-2.5-flash"\ngates = ["critic"]\n' + _delta_on("")
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "a.py").write_text("x = 1  # ONE\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")
    _review(repo)  # full round: a's finding #1 (a.py), b's #2 (b.py)
    _fix(repo, "d.py", "x = 1  # FOUR\n")  # both reviewers flag it, differently
    lines: list[str] = []
    out = api.review(repo, gate="critic", item="T1", branch="feat", on_progress=lines.append)
    assert out.exit == OK, out.reason
    # a's delta finding is the record's #3; b's is #4 -- never two #1s.
    shown = [ln.strip().split()[0] for ln in lines if ln.strip().startswith("#")]
    assert shown == ["#3", "#4"], lines
    found = _ev(repo)["chunk_findings"]
    assert [f["location"] for f in found] == ["a.py:1", "b.py:1", "d.py:1", "e.py:1"], found


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


def test_a_delta_finding_from_another_reviewer_still_records_a_reason(repo, tmp_path):
    """Bug B0419c114ef: the gate blocks on a finding from ANY reviewed reviewer, but the
    `failed` reason was read from the single ``best`` only; when the delta's finding came
    from the OTHER reviewer (``best`` reported none), the reason was empty and recording
    ``failed`` raised ValueError, so that round never persisted and the gate stayed failed
    with unresolved findings. The reason now counts the findings the gate blocked on,
    whichever reviewer raised them."""
    clean = tmp_path / "clean-reviewer"
    clean.write_text("#!/bin/sh\ncat >/dev/null\necho 'STATUS: NO FINDINGS'\n")
    clean.chmod(clean.stat().st_mode | stat.S_IXUSR)
    finder = tmp_path / "finder-reviewer"
    finder.write_text(
        "#!/bin/sh\nin=$(cat)\n"
        "case \"$in\" in *FINDME*) printf 'FINDING HIGH y.py:1\\nbad\\n\\nSTATUS: FINDINGS 1\\n';; "
        "*) echo 'STATUS: NO FINDINGS';; esac\n"
    )
    finder.chmod(finder.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        f'[[reviewer]]\nname = "clean"\nkind = "command"\ncommand = "{clean}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic"]\n'
        f'[[reviewer]]\nname = "finder"\nkind = "command"\ncommand = "{finder}"\n'
        'model = "gemini-2.5-flash"\ngates = ["critic"]\n' + _delta_on("")
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "x.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")
    first = _review(repo)  # a clean first round: the gate passes
    assert first.exit == OK, first.reason
    assert _record(repo).outcome == "passed"
    _fix(repo, "y.py", "y = 1  # FINDME\n")
    out = _review(repo)  # the delta raises a finding the OTHER reviewer; best reported none
    assert out.exit == OK, out.reason
    assert out.data["outcome"] == "failed", out.data
    rec = _record(repo)
    assert rec.outcome == "failed"
    assert rec.reason, "the failed delta round must record a reason or it never persists"
    ev = _ev(repo)
    assert len(ev["chunk_findings"]) == 1, ev["chunk_findings"]
    assert "y.py:1" in ev["chunk_findings"][0]["location"], ev["chunk_findings"]


# -- gate status and surfaces ------------------------------------------------------------
