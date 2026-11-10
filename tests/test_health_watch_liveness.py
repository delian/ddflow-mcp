"""The watchdog reports a gate/review loop as RUNAWAY (DX-health-watch-liveness).

`ddflow loops`, `doctor` and the item's brief already read `[loops].max_repeated_failures`
and name the loop. The watchdog's unattended pass runs on a cadence, so a gate loop that
will never converge -- a lane re-applying the same failing patch, a gate flapping, a
claim-and-give-up cycle -- has to surface there too, not only for a reader who happens to
look. `core.progress.loops` already detects them; this is the wiring that lets
`ddflow health` see them as RUNAWAY."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

FAIL = 1


def _setup(repo: Path) -> None:
    run_cli(repo, "init")
    cmd = f'{sys.executable} -c "import sys; print(chr(98)*3); sys.exit(1)"'
    (repo / ".ddflow" / "gates.toml").write_text(
        f'[gate.unit_tests]\ncommand = {json.dumps(cmd)}\ncwd = "repo"\n'
    )
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
    assert run_cli(repo, "claim", "T1")[0] == 0  # a live lane holds it


def _findings(repo) -> list[dict]:
    code, out, err = run_cli(repo, "--json", "health")
    assert code == 0, err
    return json.loads(out)["findings"]


def _loop_runaways(found: list[dict]) -> list[dict]:
    return [f for f in found if f.get("evidence", {}).get("loop")]


def test_a_repeated_gate_failure_is_a_runaway_in_the_health_pass(repo):
    _setup(repo)
    for _ in range(3):
        assert run_cli(repo, "gate", "run", "T1", "unit_tests")[0] == FAIL
    runaways = _loop_runaways(_findings(repo))
    assert runaways and any(f["kind"] == "runaway" for f in runaways), runaways
    assert runaways[0]["evidence"]["loop"] == "repeated_failure"
    assert runaways[0]["evidence"]["gate"] == "unit_tests"
    assert runaways[0]["item"] == "T1"


def test_one_failure_is_not_reported_as_a_loop(repo):
    _setup(repo)
    assert run_cli(repo, "gate", "run", "T1", "unit_tests")[0] == FAIL  # one run is not a loop
    assert _loop_runaways(_findings(repo)) == []
