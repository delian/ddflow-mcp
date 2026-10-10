"""Characterization of the knowledge and decision commands that run on the CLI executor
(B-uc-surf-knowledge): stdout, stderr and exit code of each, human and --json, for success,
refusal, failure and nothing-to-do. The table in tests/data was captured from the
hand-written handlers BEFORE they were replaced; a diff here is a user-visible change."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from tests.conftest import run_cli

TABLE = Path(__file__).parent / "data" / "uc_surf_knowledge_pin.json"

SEED = [
    (
        "lesson",
        "add",
        "--id",
        "L-one",
        "--title",
        "Probe before you claim",
        "--rule",
        "always probe first",
    ),
    (
        "lesson",
        "add",
        "--id",
        "L-two",
        "--title",
        "Probe before you claim again",
        "--rule",
        "always probe first",
        "--new",
    ),
    (
        "lesson",
        "add",
        "--id",
        "L-three",
        "--title",
        "Quote paths",
        "--rule",
        "quote every path with a space",
        "--new",
    ),
    (
        "decision",
        "add",
        "--id",
        "D-one",
        "--title",
        "Use X",
        "--decision",
        "we use X for storage",
        "--globs",
        "src/x/*",
    ),
    (
        "decision",
        "add",
        "--id",
        "D-two",
        "--title",
        "Use Y",
        "--decision",
        "we use Y for queues",
        "--new",
    ),
    ("task", "add", "T1", "--title", "touch x", "--globs", "src/x/a.py"),
]

SCENARIOS = [
    ("similar", "always probe first"),
    ("similar", "zzzz nothing like it qqqq"),
    ("similar", "always probe first", "--kind", "nosuchkind"),
    ("dupes",),
    ("dupes", "--floor", "0.99"),
    ("dupes", "--kind", "nosuchkind"),
    ("lesson", "search", "probe"),
    ("lesson", "search", "zzzznothingqqqq"),
    ("lesson", "verify"),
    ("link", "L-two", "--duplicate-of", "L-one"),
    ("link", "L-three", "--related", "L-one", "--reason", "same area"),
    ("link", "L-nope", "--related", "L-one"),
    ("link", "L-one", "--related", "L-one"),
    ("link", "L-one"),
    ("decision", "show", "D-one"),
    ("decision", "show", "D-nope"),
    ("decision", "applicable", "T1"),
    ("decision", "applicable", "T-nope"),
    ("decision", "supersede", "D-one", "--by", "D-two", "--reason", "y wins"),
    ("decision", "supersede", "D-nope", "--by", "D-two"),
    ("decision", "supersede", "D-two", "--by", "D-nope"),
    ("decision", "show", "D-one"),
]
# Each scenario is run twice: as typed and with the global --json before the command.


def _seeded(repo: Path) -> None:
    for argv in SEED:
        code, out, err = run_cli(repo, *argv, agent="pin")
        assert code == 0, (argv, out, err)


def _norm(text: str, repo: Path) -> str:
    text = re.sub(r"\d{4}-\d\d-\d\dT[\d:.]+Z", "<ts>", text.replace(str(repo), "<repo>"))
    return re.sub(r"\b[0-9a-f]{12,}\b", "<hash>", text)


def _table(repo: Path) -> dict[str, list]:
    _seeded(repo)
    rows: dict[str, list] = {}
    for argv in SCENARIOS:
        for json_mode in (False, True):
            full = (("--json",) if json_mode else ()) + argv
            code, out, err = run_cli(repo, *full, agent="pin")
            rows[" ".join(full)] = [code, _norm(out, repo), _norm(err, repo)]
    return rows


def test_the_executor_commands_print_what_the_handwritten_ones_did(repo: Path) -> None:
    got = _table(repo)
    if os.environ.get("U1_PIN_RECORD"):
        TABLE.parent.mkdir(exist_ok=True)
        TABLE.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n")
        pytest.skip("recorded")
    want = json.loads(TABLE.read_text())
    assert got.keys() == want.keys()
    bad = {k: (want[k], got[k]) for k in want if want[k] != got[k]}
    assert not bad, json.dumps(bad, indent=1)
