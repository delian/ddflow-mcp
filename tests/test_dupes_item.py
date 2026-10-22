"""B-dupes-cli-takes-no-item-id: `ddflow dupes <id>` scans one record against the rest.

The dedupe gate text and agents say `ddflow dupes <item-id>`; argparse used to reject the
positional. The id narrows the sweep to the pairs that contain it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

BUG_A = "the parser drops the trailing token when the line is empty"
BUG_B = "parser drops a trailing token on an empty line"
OTHER_A = "the exporter writes the wrong timezone into every generated report header"
OTHER_B = "generated report header carries the wrong timezone from the exporter"


def _repo(repo: Path) -> None:
    run_cli(repo, "init")
    for bid, text in (("B1", BUG_A), ("B2", BUG_B), ("B3", OTHER_A), ("B4", OTHER_B)):
        run_cli(repo, "bug", "found", "--id", bid, "--summary", text, "--no-task")


def test_dupes_takes_an_item_id_and_shows_only_its_pairs(repo):
    _repo(repo)
    code, out, err = run_cli(repo, "--json", "dupes", "B1")
    assert code == 0, err
    pairs = json.loads(out)["pairs"]
    assert pairs and all("B1" in (p["a"], p["b"]) for p in pairs), pairs
    assert {x for p in pairs for x in (p["a"], p["b"])} == {"B1", "B2"}


def test_dupes_item_without_a_match_is_exit_2(repo):
    _repo(repo)
    run_cli(
        repo, "bug", "found", "--id", "B9", "--summary", "unrelated gizmo calibration", "--no-task"
    )
    code, out, err = run_cli(repo, "dupes", "B9")
    assert code == 2 and "unrecognized" not in err and "B9" in out + err


def test_dupes_unknown_item_is_refused_with_the_id(repo):
    _repo(repo)
    code, _out, err = run_cli(repo, "dupes", "B-nope")
    assert code == 1 and "B-nope" in err, err


def test_dupes_without_an_id_is_still_the_whole_sweep(repo):
    _repo(repo)
    _code, out, _err = run_cli(repo, "--json", "dupes")
    pairs = json.loads(out)["pairs"]
    assert {(p["a"], p["b"]) for p in pairs} >= {("B1", "B2"), ("B3", "B4")}
