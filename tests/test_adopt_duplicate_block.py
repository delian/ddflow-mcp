"""Two managed blocks (B63635ddc6a).

An older ddflow's `adopt` that meets a newer ddflow's stamped block does not recognise the
markers and appends a SECOND block under the legacy markers, so every later refresh
flip-flops. A released version cannot be fixed; this ddflow must (1) say so in `doctor`, as a
PROBLEM naming the file, (2) collapse the blocks to exactly one on `adopt --refresh-docs`,
keeping every byte outside the markers, idempotently, and (3) say nothing about a single
healthy block.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from conftest import run_cli

from ddflow.core.events import Event
from ddflow.services.adopt import BLOCK, LEGACY_BEGIN, LEGACY_END, block_spans

FILES = ("AGENTS.md", "CLAUDE.md")
LEGACY = f"{LEGACY_BEGIN}\nOLD RULES an older ddflow wrote\n{LEGACY_END}\n"


def _adopted(repo: Path) -> Path:
    assert run_cli(repo, "adopt", "--agents", "claude")[0] == 0
    return repo


def _begin_lines(text: str) -> int:
    return len(re.findall(r"^<!-- (?:ddflow:begin rules/work-queue|DDFLOW:BEGIN)", text, re.M))


def _two_blocks(repo: Path) -> dict[str, str]:
    """What the old adopt leaves: the stamped block, then a legacy-marker block appended."""
    before: dict[str, str] = {}
    for name in FILES:
        path = repo / name
        text = "# Project notes\n\nkeep this above\n\n" + path.read_text() + "\nkeep this below\n"
        text = text.replace("\nkeep this below\n", "\nkeep this below\n\n" + LEGACY + "tail text\n")
        path.write_text(text)
        before[name] = text
    return before


def _doctor(repo: Path) -> tuple[int, str]:
    code, out, err = run_cli(repo, "doctor")
    return code, out + err


def test_doctor_reports_two_blocks_as_a_problem_naming_the_file(repo: Path):
    _adopted(repo)
    _two_blocks(repo)
    code, text = _doctor(repo)
    assert code == 1, text
    assert "AGENTS.md holds 2 managed blocks" in text
    assert "CLAUDE.md holds 2 managed blocks" in text
    assert "adopt --refresh-docs" in text


def test_doctor_notes_an_instruction_file_older_than_the_logs_highest_stamp(repo: Path):
    _adopted(repo)
    _two_blocks(repo)
    ev = Event(
        kind="ddflow.seen",
        subject="ddflow",
        data={"version": "99.0.0", "install": "installed"},
        agent="future",
        lamport=1,
        ts="2099-01-01T00:00:00.000000Z",
    )
    ev = Event(**{**ev.__dict__, "id": ev.compute_id()})
    (repo / ".ddflow" / "events" / "future.jsonl").write_text(ev.to_json() + "\n")
    _code, out = _doctor(repo)
    # reads and doctor still work under a skew; the note names the file and both versions
    assert re.search(r"AGENTS\.md.*stamp.*older.*99\.0\.0", out, re.I), out


def _outside(text: str) -> str:
    """``text`` with every managed block (both grammars) cut out, the rest byte for byte."""
    for lo, hi in reversed(block_spans(text)):
        text = text[:lo] + text[hi:]
    return text


def test_refresh_collapses_to_one_block_and_keeps_the_outside_bytes(repo: Path):
    _adopted(repo)
    before = _two_blocks(repo)
    outside = {name: _outside(text) for name, text in before.items()}
    code, out, err = run_cli(repo, "adopt", "--refresh-docs")
    assert code == 0, (out, err)
    for name in FILES:
        after = (repo / name).read_text()
        assert _begin_lines(after) == 1, after
        assert BLOCK.owns(after) and LEGACY_BEGIN not in after
        assert "OLD RULES" not in after
        # every byte outside the blocks is exactly what it was
        assert _outside(after) == outside[name]


def test_the_collapse_keeps_an_attribute_a_newer_writer_added_to_its_block(repo: Path):
    _adopted(repo)
    path = repo / "AGENTS.md"
    text = path.read_text()
    path.write_text(
        re.sub(
            r"(<!-- ddflow:begin rules/work-queue [^\n]*?) -->", r"\1 owner=x -->", text, count=1
        )
        + "\n"
        + LEGACY
    )
    assert run_cli(repo, "adopt", "--refresh-docs")[0] == 0
    after = path.read_text()
    assert _begin_lines(after) == 1 and "owner=x" in after


def test_a_column_0_pair_even_in_a_fence_counts_as_a_block_and_is_backed_up(repo: Path):
    """Pins the documented trigger: a marker pair on its own lines at column 0 is a block by
    the same rule `Region.find` always used, fenced or not; the refresh backs the file up."""
    _adopted(repo)
    path = repo / "AGENTS.md"
    path.write_text(path.read_text() + f"\n```\n{LEGACY}```\n")
    _code, out = _doctor(repo)
    assert "AGENTS.md holds 2 managed blocks" in out, out
    assert run_cli(repo, "adopt", "--refresh-docs")[0] == 0
    assert _begin_lines(path.read_text()) == 1
    backups = list((repo / ".ddflow" / "backups").rglob("AGENTS.md"))
    assert backups and "OLD RULES" in backups[0].read_text()


def test_a_marker_shown_indented_is_not_a_second_block(repo: Path):
    _adopted(repo)
    path = repo / "AGENTS.md"
    example = f"\nAn old file looked like this:\n\n    {LEGACY_BEGIN}\n    old\n    {LEGACY_END}\n"
    path.write_text(path.read_text() + example)
    _code, out = _doctor(repo)
    assert "managed blocks" not in out, out
    assert run_cli(repo, "adopt", "--refresh-docs")[0] == 0
    assert example in path.read_text()


def test_refresh_is_idempotent_after_collapsing(repo: Path):
    _adopted(repo)
    _two_blocks(repo)
    assert run_cli(repo, "adopt", "--refresh-docs")[0] == 0
    once = {n: (repo / n).read_text() for n in FILES}
    assert all(_begin_lines(t) == 1 for t in once.values())
    assert run_cli(repo, "adopt", "--refresh-docs")[0] == 0
    assert {n: (repo / n).read_text() for n in FILES} == once
    _code, text = _doctor(repo)
    assert "managed blocks" not in text, text


def test_two_stamped_blocks_collapse_too(repo: Path):
    _adopted(repo)
    path = repo / "AGENTS.md"
    text = path.read_text()
    path.write_text(text + "\nbetween\n\n" + text)
    code, out = _doctor(repo)
    assert code == 1 and "AGENTS.md holds 2 managed blocks" in out, out
    assert run_cli(repo, "adopt", "--refresh-docs")[0] == 0
    after = path.read_text()
    assert _begin_lines(after) == 1 and "between" in after


def test_a_single_healthy_block_produces_neither_line(repo: Path):
    _adopted(repo)
    _code, text = _doctor(repo)
    assert "managed blocks" not in text, text
    assert not re.search(r"AGENTS\.md.*stamp.*older", text, re.I), text


def test_a_driver_doc_without_its_header_is_a_drift_note(repo: Path):
    _adopted(repo)
    doc = repo / "docs" / "ddflow" / "drivers" / "implement-phase.md"
    body = doc.read_text()
    doc.write_text("\n".join(ln for ln in body.splitlines() if "ddflow:" not in ln) + "\n")
    _code, text = _doctor(repo)
    assert "implement-phase.md" in text, text


@pytest.mark.parametrize("name", FILES)
def test_a_newer_stamped_block_is_never_downgraded_by_the_collapse(repo: Path, name: str):
    """The duplicate collapse must not hide the format guard: a block written at a higher
    FORMAT LEVEL is refused (exit 3), and the file is left as it was."""
    _adopted(repo)
    path = repo / name
    text = path.read_text()
    newer = re.sub(r"fmt=\d+", "fmt=999", text, count=1)
    path.write_text(newer + "\n" + LEGACY)
    before = path.read_text()
    code, out, err = run_cli(repo, "adopt", "--refresh-docs")
    assert code == 3, (out, err)
    assert path.read_text() == before
