"""A skipped gate auto-files ONE follow-up task to re-run that gate later.

The follow-up's scope is only to RE-RUN the gate on the item (`ddflow gate run <id>
<gate>` and record the honest outcome) -- never to repair the gate-check code, never to
redo the item's payload. It is dropped when the item is abandoned; completing the item
or re-recording the gate non-skipped also settles it.

Configurable per ``[gates].skip_followup`` = auto | warn | off (default auto), respecting
the company -> project -> local override order. These are the regression tests for that
behaviour: a skip only ADDS the follow-up, so a skip today records nothing extra.
"""

from conftest import run_cli, write_config

from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def _fold(repo):
    return fold(EventLog(repo).read_all(), strict=False)


def _followups(repo, item: str):
    """Every OWED follow-up task that re-runs a gate on ``item``, oldest first.

    Mirrors the production ``_open``: neither removed nor finished (terminal) counts."""
    st = _fold(repo)
    return [
        i
        for i in st.items.values()
        if getattr(i, "followup_of", "") == item and not i.removed and not i.terminal
    ]


def _add(repo, item: str = "T1"):
    run_cli(repo, "task", "add", item, "--title", f"{item} fixture", "--globs", "a.py")
    return _fold(repo)


def test_a_gate_skip_autofiles_one_followup(repo):
    """Skipping a gate files exactly ONE follow-up task that re-runs that gate."""
    _add(repo)
    code, out, err = run_cli(
        repo, "gate", "skip", "T1", "dedupe", "--reason", "no shared code touched"
    )
    assert code == 0, f"{out}{err}"
    fups = _followups(repo, "T1")
    assert len(fups) == 1, f"expected exactly one follow-up, got {[f.id for f in fups]}"
    assert fups[0].followup_gate == "dedupe", fups[0]
    assert "re-run" in fups[0].title.lower() or "re-run" in fups[0].body.lower()


def test_a_gate_skip_autofiles_none_when_disabled(repo):
    """`skip_followup = off` files no follow-up for the same skip."""
    _add(repo)
    write_config(repo, [("gates.skip_followup", "off")])
    code, out, err = run_cli(
        repo, "gate", "skip", "T1", "dedupe", "--reason", "no shared code touched"
    )
    assert code == 0, f"{out}{err}"
    assert _followups(repo, "T1") == []


def test_abandoning_the_item_drops_the_followup(repo):
    """The follow-up is dropped when its item is abandoned."""
    _add(repo)
    run_cli(repo, "gate", "skip", "T1", "dedupe", "--reason", "no shared code touched")
    fups = _followups(repo, "T1")
    assert len(fups) == 1

    code, out, err = run_cli(repo, "abandon", "T1", "--reason", "decided against it")
    assert code == 0, f"{out}{err}"
    st = _fold(repo)
    assert all(st.items[f.id].removed for f in fups), [(f.id, st.items[f.id].removed) for f in fups]


def test_rerecording_the_gate_non_skipped_settles_the_followup(repo):
    """Recording the gate's outcome as a non-skip settles the follow-up."""
    _add(repo)
    run_cli(repo, "gate", "skip", "T1", "dedupe", "--reason", "deferred")
    fups = _followups(repo, "T1")
    assert len(fups) == 1

    code, out, err = run_cli(
        repo,
        "gate",
        "record",
        "T1",
        "dedupe",
        "--outcome",
        "passed",
        "--evidence",
        "checked duplicates",
    )
    assert code == 0, f"{out}{err}"
    st = _fold(repo)
    assert all(st.items[f.id].removed for f in fups), [(f.id, st.items[f.id].removed) for f in fups]


def test_warn_reports_but_files_nothing(repo):
    """`skip_followup = warn` mentions the follow-up in the output and files none."""
    _add(repo)
    write_config(repo, [("gates.skip_followup", "warn")])
    code, out, err = run_cli(
        repo, "gate", "skip", "T1", "dedupe", "--reason", "no shared code touched"
    )
    assert code == 0, f"{out}{err}"
    assert "skip_followup" in f"{out}{err}", (
        f"the skip output must report the un-filed follow-up: {out}{err}"
    )
    assert _followups(repo, "T1") == []


def test_a_finished_followup_is_not_an_open_rerun(repo):
    """A follow-up that reached a terminal state is not 'owed': skipping the gate again
    files a FRESH follow-up, not 'already re-runs'. Reverts to not filtering `terminal`
    would make the second skip file nothing."""
    _add(repo)
    code, out, err = run_cli(
        repo, "gate", "skip", "T1", "dedupe", "--reason", "no shared code touched"
    )
    assert code == 0, f"{out}{err}"
    first = _followups(repo, "T1")
    assert len(first) == 1

    # Abandon the follow-up ITSELF (terminal, not removed): it is done with, but so is
    # its claim to be the re-run owed for T1.
    code, out, err = run_cli(repo, "abandon", first[0].id, "--reason", "re-run happened elsewhere")
    assert code == 0, f"{out}{err}"

    code, out, err = run_cli(
        repo, "gate", "skip", "T1", "dedupe", "--reason", "still no shared code touched"
    )
    assert code == 0, f"{out}{err}"
    fups = _followups(repo, "T1")
    assert len(fups) == 1, f"expected one fresh follow-up, got {[f.id for f in fups]}"
    assert fups[0].id != first[0].id
