"""`ddflow schedule trigger list|show|evaluate`, byte for byte (B-uc-surf-reporting).

The schedule CLI is planned work (P-schedule): its commands stay as they behave today. This
pins the trigger verbs' human and `--json` output, their streams and exit codes before the
function behind them is split.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import run_cli

from ddflow.api import schedule as A
from ddflow.surfaces import cli
from ddflow.surfaces.commands.schedule import add_schedule_parser
from ddflow.surfaces.context import Ctx

#: The broken trigger file is reported on stderr by every verb that loads the triggers.
PROBLEM = "problem: .ddflow/triggers/bad.toml: cannot read it: Invalid value (at end of document)\n"


@pytest.fixture
def proj(repo: Path) -> Path:
    code, out, err = run_cli(repo, "init")
    assert code == 0, (code, out, err)
    job = {"title": "Fix it", "cadence": {"every_days": 7}, "scope_globs": ["src/**"]}
    assert A.schedule_define(repo, "fix", job).exit == 0
    d = repo / ".ddflow" / "triggers"
    d.mkdir(parents=True)
    (d / "red.toml").write_text(
        'event = "gate.failed"\nkey = "{subject}"\naction = { job = "fix" }\nenabled = true\n'
    )
    (d / "off.toml").write_text('event = "gate.failed"\naction = { job = "fix" }\n')
    (d / "bad.toml").write_text("event = [\n")
    assert run_cli(repo, "task", "add", "T1", "--globs", "a.py")[0] == 0
    gate = ("gate", "record", "T1", "unit_tests", "--outcome", "failed", "--reason", "red")
    assert run_cli(repo, *gate)[0] == 0
    return repo


def _cli(proj: Path, capsys, *argv: str) -> tuple[int, str, str]:
    p = cli.build_parser()
    sub = next(x for x in p._actions if isinstance(x, argparse._SubParsersAction))
    add_schedule_parser(sub)
    a = p.parse_args(["--repo", str(proj), *argv])
    capsys.readouterr()
    code = int(a.fn(a, Ctx(a)))
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def _steady(text: str) -> str:
    text = re.sub(r"T-red-[0-9a-f]+", "T-red-<ID>", text)
    return re.sub(r"\d{4}-\d\d-\d\dT[\d:.]+(Z|\+00:00)", "<AT>", text)


SUPPRESSED = "suppressed off: disabled (enable it with enabled = true in its file)\n"


def test_the_trigger_verbs_print_what_they_always_printed(proj, capsys):
    soon = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()

    def run(*argv):
        code, out, err = _cli(proj, capsys, *argv)
        return code, _steady(out), _steady(err)

    assert run("schedule", "trigger") == (
        0,
        f"{'off':<22} {'gate.failed':<20} off fires 0\n{'red':<22} {'gate.failed':<20} on  fires 0\n",
        PROBLEM,
    )
    code, out, err = run("--json", "schedule", "trigger", "list")
    assert (code, err) == (0, "")
    assert [r["id"] for r in json.loads(out)] == ["off", "red"]
    dry = ("schedule", "trigger", "evaluate", "--dry-run", "--now", soon)
    assert run(*dry) == (0, SUPPRESSED + "FIRED      red [T1]: filed \n", PROBLEM)
    real = ("schedule", "trigger", "evaluate", "--now", soon)
    assert run(*real) == (0, SUPPRESSED + "FIRED      red [T1]: filed T-red-<ID>\n", PROBLEM)
    assert run(*real) == (0, SUPPRESSED, PROBLEM)
    assert run("schedule", "trigger", "show", "red") == (
        0,
        "red  \n  event gate.failed  count 1  window 0m  key {subject}  job fix\n"
        "  enabled  fires 1  open T-red-<ID>\n",
        "",
    )
    assert run("schedule", "trigger", "show", "off") == (
        0,
        "off  \n  event gate.failed  count 1  window 0m  key -  job fix\n"
        "  disabled  fires 0  open -\n"
        "  suppressed <AT> -: disabled\n  suppressed <AT> -: disabled\n",
        "",
    )
    code, out, err = run("--json", "schedule", "trigger", "show", "red")
    assert (code, err) == (0, "") and json.loads(out)["fires"] == 1
    assert run("schedule", "trigger", "show", "nope") == (1, "", "no such trigger 'nope'\n")
    assert run("schedule", "trigger", "evaluate", "--now", "yesterday") == (
        1,
        "",
        "--now 'yesterday' is not an ISO timestamp\n",
    )
