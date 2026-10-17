"""Every recorded CLI transcript is data a pin recipe can re-record (DX-pins-uncovered)."""

from __future__ import annotations

from pathlib import Path

from pin_norm import rerecord

from ddflow.config import Config
from ddflow.services import pins as PN

ROOT = Path(__file__).resolve().parents[1]


def test_the_transcripts_and_the_readme_command_list_each_have_a_recipe():
    files = PN.pin_files(ROOT, Config.load(ROOT))
    for f in (
        "tests/test_uc_surf_loop_transcript.json",
        "tests/test_uc_surf_setup_ops_transcript.json",
        "README.md",
    ):
        assert f in files, f"{f} is a pin file without a regeneration recipe"
    names = {r.name for r in PN.load_recipes(ROOT, Config.load(ROOT))}
    assert {"loop-transcript", "readme-commands"} <= names


def test_the_loop_transcript_expectations_are_data_not_source():
    source = (ROOT / "tests" / "test_uc_surf_loop_transcript.py").read_text("utf-8")
    assert "EXPECTED = [" not in source and 'with_suffix(".json")' in source


def test_rerecord_replaces_only_the_rows_that_differ():
    want = [[["a"], 0, "old\n", ""], [["b"], 0, "same\n", ""], [["a"], 0, "second\n", ""]]
    got = [
        [["a"], 0, "new\n", ""],
        [["b"], 0, "same\n", ""],
        [["a"], 0, "second\n", ""],
        [["c"], 1, "", "x"],
    ]
    merged = rerecord(got, want)
    assert merged == got and merged[1] is want[1] and merged[2] is want[2]
    assert rerecord(want, want) == want
