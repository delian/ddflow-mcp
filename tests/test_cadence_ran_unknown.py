"""An unknown `cadence --ran` name is refused, not recorded as a silent no-op.

`ddflow cadence --ran NOPE` used to exit 0 and print "recorded cadence run: NOPE",
appending a `cadence.ran` event for a pass that is not configured -- a typo such as
`dedupe_swep` reported success while the real `dedupe_sweep` stayed due, and the log
gained a run of nothing (bug Be585bd4631). An unknown name must fail, naming the known
passes.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

OK, FAIL, NOTHING = 0, 1, 2


def test_an_unknown_cadence_name_is_refused_and_names_the_known_passes(repo):
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "cadence", "--ran", "NOPE")
    assert code == FAIL, (code, out, err)
    assert "NOPE" in out + err
    # the known count passes are offered, so the operator can correct a typo
    assert "dedupe_sweep" in out + err


def test_a_known_count_pass_is_still_recorded(repo):
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "cadence", "--ran", "dedupe_sweep")
    assert code == OK, (code, out, err)
    assert "recorded cadence run: dedupe_sweep" in out


def test_lessons_compression_is_still_recorded(repo):
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "cadence", "--ran", "lessons_compression")
    assert code == OK, (code, out, err)
    assert "recorded cadence run: lessons_compression" in out
