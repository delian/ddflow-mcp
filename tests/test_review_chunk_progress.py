"""B53b653895f: chunk progress is ordered by events, never by a wall-clock margin.

Split from tests/test_review_chunks.py, which is at the file-size limit.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from test_review_chunks import _api_repo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _gated_second(gate: Path, quick_delay: float = 0) -> str:
    """A reviewer whose SLOWCHUNK answer waits for a file the test creates, not for a guessed
    number of seconds: the quick chunk can only be reported first if it really settled alone.
    The loop is a ceiling for a wedged run (B53b653895f). ``quick_delay`` is a load injected
    into the quick chunk."""
    from _wait import scaled

    polls = int(scaled(30) / 0.05)
    return (
        f'case "$in" in *SLOWCHUNK*) n=0; while [ ! -e {gate} ] && [ $n -lt {polls} ]; do '
        f"sleep 0.05; n=$((n+1)); done;; *) sleep {quick_delay};; esac\necho 'STATUS: NO FINDINGS'"
    )


def _first_chunk_order(lines: list[str]) -> tuple[int | None, int | None]:
    """The index in ``lines`` of the quick chunk's and of the slow chunk's settled line."""

    def at(name: str) -> int | None:
        return next((i for i, line in enumerate(lines) if "chunk" in line and name in line), None)

    return at("a.py"), at("b.py")


def test_the_settle_order_is_by_line_not_by_clock():
    """The ci run that flaked (B8858b1b659): under load start-up took 4.4 s and the quick
    chunk settled 0.1 s before the slow one. The order of the lines is the evidence."""
    streamed = ["→ fake reviewing", "  chunk 3/4 (a.py): reviewed", "  chunk 4/4 (b.py): reviewed"]
    assert _first_chunk_order(streamed) == (1, 2)
    assert _first_chunk_order(["  chunk 4/4 (b.py): reviewed", "  chunk 3/4 (a.py): reviewed"]) == (
        1,
        0,
    )


@pytest.mark.parametrize("quick_delay", [0, 3], ids=["quiet", "quick-chunk-delayed-3s"])
def test_progress_is_reported_as_each_chunk_settles(repo, tmp_path, monkeypatch, quick_delay):
    """The slow chunk is held until the quick chunk's line AND a 'waiting' line have been
    reported, so the order is forced by events: a review that batched its output would
    never release the slow chunk before its ceiling and the quick line would not precede it.
    With the quick chunk delayed 3 s (a loaded host) the old wall-clock assertion, which wanted
    the quick line 1.5 s ahead of a slow chunk that took 3.5 s, fails; this one cannot."""
    import ddflow.api.review as api

    gate = tmp_path / "release-slow-chunk"
    _api_repo(repo, tmp_path, _gated_second(gate, quick_delay))
    (repo / "a.py").write_text("x = 1  # quick " + "p" * 60 + "\n")
    (repo / "b.py").write_text("y = 2  # SLOWCHUNK " + "p" * 60 + "\n")
    lines: list[str] = []

    def on_progress(line: str) -> None:
        lines.append(line)
        quick, slow = _first_chunk_order(lines)
        if quick is not None and slow is None and any("waiting" in x for x in lines):
            gate.write_text("go")

    out = api.review(repo, gate="critic", item="T1", on_progress=on_progress)
    assert out.data["outcome"] == "passed", out.reason
    quick, slow = _first_chunk_order(lines)
    assert quick is not None and slow is not None and quick < slow, f"order: {lines}"
    assert any("waiting" in line for line in lines[:slow]), f"no word while waiting: {lines}"
