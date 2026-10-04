"""The verify documentation matches what the commands do (B-verify-docs)."""

from __future__ import annotations

import re
from pathlib import Path

from conftest import run_cli

ROOT = Path(__file__).resolve().parents[1]
HELP = (ROOT / "ddflow/templates/prompts/help/verify.md").read_text("utf-8")


def test_the_help_topic_is_listed_and_resolves():
    code, out, _ = run_cli(ROOT, "help")
    assert "verify" in out
    code, page, _ = run_cli(ROOT, "help", "verify")
    assert code == 0 and "A `done` mark is a claim" in page


def test_every_flag_the_topic_names_is_a_real_verify_flag():
    flags = set(re.findall(r"(?<![\w-])--[a-z][a-z-]*", HELP)) - {"--sha", "--force", "--reason"}
    _code, out, _ = run_cli(ROOT, "verify", "--help")
    for flag in flags:
        assert flag in out, f"help verify names {flag}, which `ddflow verify --help` does not have"


def test_every_claim_the_topic_lists_is_a_claim_verify_can_make():
    from ddflow.services import verify as V

    produced = {
        "landed",
        "declared_files",
        "tests",
        "gates",
        "survives",
        "regression",
        "ledger",
    }
    listed = set(
        re.findall(
            r"^    (\w+)\s{2,}", HELP.split("It checks seven things")[1].split("Exit 1")[0], re.M
        )
    )
    assert listed == produced
    src = Path(V.__file__).read_text("utf-8")
    for claim in produced:
        assert f'"{claim}"' in src, claim


def test_the_driver_notes_mention_verify_and_both_copies_agree():
    a = (ROOT / "ddflow/templates/drivers/implement-phase.md").read_text("utf-8")
    b = (ROOT / "docs/ddflow/drivers/implement-phase.md").read_text("utf-8")
    assert "ddflow verify <id> --reopen" in a and a == b


def test_the_readme_names_every_verify_flag():
    readme = (ROOT / "README.md").read_text("utf-8")
    for flag in ("--all", "--phase", "--file-bugs", "--reopen", "--pack", "--judge"):
        assert f"`{flag}" in readme or flag in readme, flag
