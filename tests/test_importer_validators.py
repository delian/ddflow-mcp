"""Imported research and memories obey the rules the adders enforce (B021a859d56).

`apply_import` wrote a scraped CONFIRMED/REFUTED verdict with no probe -- which
`research_add` refuses as "an opinion wearing a label" -- cut research claims to 600
characters, and cut memories to 4000 characters, while `memory add` (and the onboarding
harness) refuse a memory over `[memory] max_chars` rather than truncate it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import importer as IM


def _log(repo: Path) -> EventLog:
    (repo / ".ddflow").mkdir(exist_ok=True)
    return EventLog(repo)


def _research(verdict: str, claim: str = "the cache is per-process") -> IM.Found:
    return IM.Found(
        kind="research",
        ident="R-cache",
        title="Is the cache shared?",
        source="docs/research.md:3",
        body=claim,
        extra={"verdict": verdict},
    )


def test_a_scraped_confirmed_verdict_with_no_probe_is_imported_as_theoretical(tmp_path):
    log = _log(tmp_path)
    IM.apply_import(tmp_path, log, IM.ImportPlan(found=[_research("CONFIRMED")]))
    note = fold(log.read_all()).research["R-cache"]
    assert note.verdict == "THEORETICAL", note.verdict
    assert note.sources == ["docs/research.md:3"]
    assert "source-verdict:CONFIRMED" in note.tags, note.tags


def test_a_scraped_theoretical_verdict_is_kept(tmp_path):
    log = _log(tmp_path)
    IM.apply_import(tmp_path, log, IM.ImportPlan(found=[_research("THEORETICAL")]))
    assert fold(log.read_all()).research["R-cache"].verdict == "THEORETICAL"


def test_a_research_claim_is_not_cut(tmp_path):
    log = _log(tmp_path)
    claim = "x" * 599 + " the end of the claim"
    IM.apply_import(tmp_path, log, IM.ImportPlan(found=[_research("THEORETICAL", claim)]))
    assert fold(log.read_all()).research["R-cache"].claim == claim


def test_a_memory_over_max_chars_is_not_recorded_or_truncated(tmp_path):
    log = _log(tmp_path)
    long = IM.Found(kind="memory", ident="M-0001", title="", source="mem.jsonl:1", body="y" * 300)
    short = IM.Found(
        kind="memory", ident="M-0002", title="", source="mem.jsonl:2", body="box has 8 GPUs"
    )
    counts = IM.apply_import(tmp_path, log, IM.ImportPlan(found=[long, short]))
    memories = fold(log.read_all()).memories
    assert "M-0001" not in memories, "a memory over [memory] max_chars (280) was recorded"
    assert memories["M-0002"].text == "box has 8 GPUs"
    assert counts.get("memory") == 1, counts
    assert any("max_chars" in k for k in counts), counts
