"""A pin never records the foreign-launch guard's refusal as its expected output (Bc28808f9c3).

Run from a linked worktree whose code is not the caller's own, the golden transcripts recorded
`ddflow: imported from the linked worktree <path> ...` for every command, with the path of
whichever worktree regenerated last, and `pins regenerate` reported success (the test passed
while it rewrote its own pin). The regeneration is refused and its files are put back.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ddflow.config import Config
from ddflow.services import install_info as I
from ddflow.services import pins as PN

PY = sys.executable


@pytest.fixture
def tree(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)  # a regeneration refuses xdist
    r = tmp_path / "proj"
    r.mkdir()
    subprocess.run(["git", "-C", str(r), "init", "-q", "-b", "main"], check=True)
    (r / "pin.json").write_text('{"ok": 1}\n')
    return r


def _recipe(regen: str) -> PN.Recipe:
    # the check always reports stale, so the regeneration runs
    return PN.Recipe("pin", ("pin.json",), (PY, "-c", "raise SystemExit(1)"), (PY, "-c", regen))


def test_a_regeneration_that_records_the_guard_refusal_is_refused_and_put_back(tree):
    refusal = f"ddflow: {I.FOREIGN_LAUNCH_REFUSAL} /somewhere/lane, not the primary"
    rep = PN.regenerate(
        tree,
        Config.load(tree),
        recipes=[_recipe(f"open('pin.json','w').write({refusal!r})")],
    )
    assert [p.name for p in rep.failed] == ["pin"], rep
    assert "foreign-launch refusal" in rep.failed[0].error
    assert (tree / "pin.json").read_text() == '{"ok": 1}\n'  # put back as it was


def test_a_regeneration_that_records_ordinary_output_is_kept(tree):
    rep = PN.regenerate(
        tree,
        Config.load(tree),
        recipes=[_recipe("open('pin.json','w').write('{\"ok\": 2}\\n')")],
    )
    assert not rep.failed or "foreign-launch" not in rep.failed[0].error
    assert (tree / "pin.json").read_text() == '{"ok": 2}\n'


def test_text_the_pin_already_quoted_is_not_a_recorded_refusal(tree):
    """Only a refusal this run WROTE counts: a pin that legitimately quotes the message (or a
    file the run left alone) must not be rolled back."""
    quoted = f"docs: the guard says {I.FOREIGN_LAUNCH_REFUSAL} X\n"
    (tree / "pin.json").write_text(quoted)
    rep = PN.regenerate(
        tree,
        Config.load(tree),
        recipes=[_recipe(f"open('pin.json','w').write({quoted + 'more'!r})")],
    )
    assert not rep.failed or "foreign-launch" not in rep.failed[0].error
    assert (tree / "pin.json").read_text().endswith("more")


def test_a_regeneration_that_adds_more_refusals_to_a_pin_that_quoted_one_is_refused(tree):
    quoted = f"docs: the guard says {I.FOREIGN_LAUNCH_REFUSAL} X\n"
    (tree / "pin.json").write_text(quoted)
    poisoned = quoted + f"ddflow: {I.FOREIGN_LAUNCH_REFUSAL} /lane\n"
    rep = PN.regenerate(
        tree, Config.load(tree), recipes=[_recipe(f"open('pin.json','w').write({poisoned!r})")]
    )
    assert "foreign-launch refusal" in rep.failed[0].error
    assert (tree / "pin.json").read_text() == quoted


def test_a_replacement_with_the_refusal_line_is_refused_even_at_the_same_phrase_count(tree):
    """A pin that quoted the guard's full line (with a documenting prefix) and a run that
    REPLACED it with the bare refusal line have the same phrase count; the refusal line this
    run wrote is still refused (the anchored ``ddflow: `` line was not in the pin before)."""
    documented = f"example: ddflow: {I.FOREIGN_LAUNCH_REFUSAL} old\n"
    (tree / "pin.json").write_text(documented)
    refusal = f"ddflow: {I.FOREIGN_LAUNCH_REFUSAL} /lane\n"
    rep = PN.regenerate(
        tree, Config.load(tree), recipes=[_recipe(f"open('pin.json','w').write({refusal!r})")]
    )
    assert rep.failed and "foreign-launch refusal" in rep.failed[0].error
    assert (tree / "pin.json").read_text() == documented
