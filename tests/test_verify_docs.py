"""The verify documentation matches what the commands do (B-verify-docs)."""

from __future__ import annotations

import re
from pathlib import Path

from conftest import run_cli

from ddflow.services import adopt as AD

ROOT = Path(__file__).resolve().parents[1]
HELP = (ROOT / "ddflow/templates/prompts/help/verify.md").read_text("utf-8")


def test_the_help_topic_is_listed_and_resolves():
    code, out, _ = run_cli(ROOT, "help")
    assert "verify" in out
    code, page, _ = run_cli(ROOT, "help", "verify")
    assert code == 0 and "A `done` mark is a claim" in page


def test_every_flag_the_topic_names_is_a_real_verify_flag():
    # `--sha` is `complete`'s flag, named in the topic; every other flag is verify's own.
    named = set(re.findall(r"(?<![\w-])--[a-z][a-z_-]*", HELP)) - {"--sha"}
    _code, out, _ = run_cli(ROOT, "verify", "--help")
    real = set(re.findall(r"(?<![\w-])--[a-z][a-z_-]*", out))
    assert named <= real, f"help verify names flags verify does not have: {sorted(named - real)}"
    assert {"--all", "--phase", "--file-bugs", "--reopen", "--pack", "--judge", "--force"} <= named


def test_every_claim_the_topic_lists_is_a_claim_verify_can_make():
    from ddflow.services import verify as V

    produced = {
        "landed",
        "declared_files",
        "tests",
        "gates",
        "survives",
        "regression",
        "requirement",
        "ledger",
    }
    listed = set(
        re.findall(
            r"^    (\w+)\s{2,}", HELP.split("It checks eight things")[1].split("Exit 1")[0], re.M
        )
    )
    assert listed == produced
    src = Path(V.__file__).read_text("utf-8")
    for claim in produced:
        assert f'"{claim}"' in src, claim


def test_the_driver_notes_mention_verify_and_both_copies_agree():
    a = (ROOT / "ddflow/templates/drivers/implement-phase.md").read_text("utf-8")
    rel = "docs/ddflow/drivers/implement-phase.md"
    b = AD._region_body(AD._driver_region(rel), (ROOT / rel).read_text("utf-8"))  # the shipped text
    assert "ddflow verify <id> --reopen" in a and a == b


def test_the_readme_names_every_verify_flag():
    readme = (ROOT / "README.md").read_text("utf-8")
    # In the verify row itself: `--force` and `--reason` are named all over the README.
    row = next(ln for ln in readme.splitlines() if "check a done task really is done" in ln)
    for flag in (
        "--all",
        "--phase",
        "--file-bugs",
        "--reopen",
        "--reason",
        "--force",
        "--pack",
        "--judge",
    ):
        assert re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", row), flag
