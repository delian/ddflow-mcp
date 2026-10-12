"""What `import` WRITES for a task, lesson and decision: whole-phrase titles and the item's
own text (B97ee75909a, imp-titles-truncated-no-text, imp-parent-id-from-heading)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import importer as IM

WORDS = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron"


def _apply(repo: Path):
    plan = IM.plan_import(repo, fold(EventLog(repo).read_all()))
    IM.apply_import(repo, EventLog(repo, agent_id="ddflow-import"), plan)
    return fold(EventLog(repo).read_all(), strict=False)


def test_written_task_keeps_whole_title_and_the_bullet_text(repo):
    (repo / "docs").mkdir()
    (repo / "docs" / "todo.md").write_text(
        "## Phase 39 follow-ups (not started)\n\n"
        "- [ ] **36.4z** Every family ships its **paired hard negative**. C2/C3 negatives\n"
        "  are wrong for a family whose positive set is defined by the absence of a marker.\n"
    )
    st = _apply(repo)
    item = st.items["36.4z"]
    assert item.title == "Every family ships its paired hard negative."
    assert "absence of a marker." in item.body
    assert item.body.rstrip().endswith("Imported from docs/todo.md:3.")
    parent = st.items[item.parent]
    assert parent.id == "PHASE-39-FOLLOW-UPS-NOT-STARTED", parent.id


def test_a_long_decision_title_is_cut_at_a_word(repo):
    (repo / "docs" / "adr").mkdir(parents=True)
    long = " ".join([WORDS] * 3)
    (repo / "docs" / "adr" / "0001-x.md").write_text(f"# {long}\n\n## Decision\nDo it.\n")
    plan = IM.plan_import(repo, fold(EventLog(repo).read_all()))
    (d,) = [f for f in plan.found if f.kind == "decision"]
    assert len(d.title) <= 141
    assert d.title.endswith("…")
    assert d.title[:-1].split()[-1] in WORDS.split(), d.title


def test_cut_title_is_whole_words_and_closes_no_backtick():
    assert IM._cut_title("short") == "short"
    cut = IM._cut_title("word " * 10 + "`a b` tail", 53)
    assert cut.count("`") % 2 == 0 and cut.endswith("…")
    assert IM._cut_title("x" * 200, 50) == "x" * 50 + "…"


def test_a_long_optmem_note_is_cut_at_a_word(repo):
    (repo / ".agent_memory").mkdir()
    text = " ".join([WORDS] * 3)
    (repo / ".agent_memory" / "LOG.txt").write_text(f"#0 2026-07-31 {text}".ljust(317) + "\n")
    plan = IM.plan_import(repo, fold(EventLog(repo).read_all()))
    notes = [f for f in plan.found if f.kind == "memory"]
    assert notes and all(n.title.endswith("…") for n in notes if len(text) > 120), [
        n.title for n in notes
    ]
    assert not any(n.title[:-1].endswith(" ka") for n in notes)
