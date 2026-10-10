"""Characterization of the knowledge and decision commands that run on the CLI executor
(B-uc-surf-knowledge): stdout, stderr and exit code of each, human and --json, for success,
refusal, failure and nothing-to-do. tests/data/uc_surf_knowledge_pin.json was captured from
the hand-written handlers BEFORE they were replaced, except six rows that carry the
corrected behaviour of two filed bugs (B6fdead1799: an unknown --kind fails on stderr with no
body; B29bee09eba: a refused link under --json carries its refusal) -- their regression tests
are in test_uc_surf_knowledge_failures.py. Any other diff here is a user-visible change."""

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
        "lesson",
        "add",
        "--id",
        "L-four",
        "--title",
        "Pin the clock",
        "--rule",
        "inject time never read it",
        "--new",
    ),
    (
        "lesson",
        "add",
        "--id",
        "L-five",
        "--title",
        "Seed the dice",
        "--rule",
        "random needs a fixed seed",
        "--new",
    ),
    (
        "lesson",
        "add",
        "--id",
        "L-six",
        "--title",
        "Close the file",
        "--rule",
        "use a context manager for handles",
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
    (
        "decision",
        "add",
        "--id",
        "D-three",
        "--title",
        "Use Z",
        "--decision",
        "we use Z for caches",
        "--new",
    ),
    ("task", "add", "T1", "--title", "touch x", "--globs", "src/x/a.py"),
]

#: (argv, modes): "h" human, "j" --json, "hj" both. A command that changes state is run in
#: ONE mode per target, so each mode pins the first (success) application, never a repeat.
SCENARIOS = [
    (("similar", "always probe first"), "hj"),
    (("similar", "zzzz nothing like it qqqq"), "hj"),
    (("similar", "always probe first", "--kind", "nosuchkind"), "hj"),
    (("dupes",), "hj"),
    (("dupes", "--floor", "0.99"), "hj"),
    (("dupes", "--floor", "1.5"), "hj"),
    (("dupes", "--kind", "nosuchkind"), "hj"),
    (("lesson", "search", "probe"), "hj"),
    (("lesson", "search", "zzzznothingqqqq"), "hj"),
    (("lesson", "verify"), "hj"),
    (("link", "L-two", "--duplicate-of", "L-one"), "h"),
    (("link", "L-five", "--duplicate-of", "L-four"), "j"),
    (("link", "L-three", "--related", "L-one", "--reason", "same area"), "h"),
    (("link", "L-six", "--related", "L-four", "--reason", "same area"), "j"),
    (("link", "L-nope", "--related", "L-one"), "hj"),
    (("link", "L-one", "--related", "L-one"), "hj"),
    (("link", "L-one"), "hj"),
    (("decision", "show", "D-one"), "hj"),
    (("decision", "show", "D-nope"), "hj"),
    (("decision", "applicable", "T1"), "hj"),
    (("decision", "applicable", "T-nope"), "hj"),
    (("decision", "supersede", "D-one", "--by", "D-two", "--reason", "y wins"), "h"),
    (("decision", "supersede", "D-three", "--by", "D-two"), "j"),
    (("decision", "supersede", "D-nope", "--by", "D-two"), "hj"),
    (("decision", "show", "D-one"), "hj"),
    (("decision", "show", "D-three"), "hj"),
]


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
    for argv, modes in SCENARIOS:
        for json_mode in (False, True):
            if ("j" if json_mode else "h") not in modes:
                continue
            full = (("--json",) if json_mode else ()) + argv
            code, out, err = run_cli(repo, *full, agent="pin")
            key = " ".join(full)
            while key in rows:  # a scenario repeated after a state change is a second row
                key += " (again)"
            rows[key] = [code, _norm(out, repo), _norm(err, repo)]
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
