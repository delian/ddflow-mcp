"""Imported research and memories obey the rules the adders enforce (B021a859d56).

`apply_import` wrote a scraped CONFIRMED/REFUTED verdict with no probe -- which
`research_add` refuses as "an opinion wearing a label" -- cut research claims to 600
characters, and cut memories to 4000 characters, while `memory add` (and the onboarding
harness) refuse a memory over `[memory] max_chars` rather than truncate it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

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


def test_an_over_long_memory_is_left_out_of_the_plan_so_verify_sees_no_drift(repo):
    """Dropped at apply but still proposed by the plan, it was re-proposed by every scan
    and `import --verify` reported drift no re-import could clear (roborev on 57449fa)."""
    (repo / ".agent_memory").mkdir()
    (repo / ".agent_memory" / "LOG.txt").write_text(
        f"#0 2026-07-31 this box has 8 H200 GPUs\n#1 2026-08-01 {'w' * 300}\n"
    )
    plan = IM.plan_import(repo)
    assert [f.ident for f in plan.by_kind("memory")] == ["M-0000"]
    assert any("M-0001" in n and "max_chars" in n for n in plan.notes), plan.notes
    code, out, err = run_cli(repo, "import", "--apply")
    assert code == 0, err
    assert "max_chars" in out, f"the apply report must say a memory was left out:\n{out}"
    code, out, err = run_cli(repo, "--json", "import", "--verify")
    report = json.loads(out)
    assert code == 0 and report["verified"], out + err
