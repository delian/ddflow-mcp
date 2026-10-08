"""The migrations registered by ddflow itself (B-uni-compat-migrations.3-seeds).

`stale-references`: the deprecated command and tool names in files and regions ddflow wrote
are rewritten on `upgrade --apply`; the project's own text and a hand-edited region are not.
Each seed is run on a fixture project written as an older ddflow left it: detected, planned
(the files it would rewrite), backed up first, applied, verified, idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import Config
from ddflow.infra.fsio import Managed
from ddflow.infra.log import EventLog
from ddflow.services import compat_refs as C
from ddflow.services import migrations as M
from ddflow.services.migrations import refs as R

VOCAB = C.Vocabulary(
    commands=frozenset({("claim",), ("docs",), ("docs", "show"), ("gate",), ("gate", "record")}),
    tools=frozenset({"ddflow_next"}),
    command_aliases={("doc",): C.Renamed("docs", "0.2.0", "1.0")},
    tool_aliases={"ddflow_old_next": C.Renamed("ddflow_next", "0.2.0", "1.0")},
)
ID = "stale-references"


def _managed(body: str) -> str:
    return (
        "# Rules\n\nmine: `ddflow doc show`\n\n" + Managed("rules/work-queue").render(body) + "\n"
    )


@pytest.fixture
def old(repo: Path) -> Path:
    assert run_cli(repo, "init")[0] == 0
    (repo / "AGENTS.md").write_text(_managed("Run `ddflow doc show`, then ddflow_old_next.\n"))
    return repo


@pytest.fixture(autouse=True)
def vocabulary():
    R.provide(lambda: VOCAB)
    yield
    R.provide(None)


def _ctx(repo: Path) -> M.Context:
    return M.context(repo, EventLog(repo, "migrator"), Config.load(repo))


def test_the_seed_is_registered_with_a_valid_declaration() -> None:
    m = M.by_id(ID)
    assert m.kinds == ("references",) and m.format_level >= 1 and m.since_version == "0.2.0"


def test_detect_and_plan_see_only_ddflows_own_regions(old: Path) -> None:
    (got,) = [p for p in M.pending(_ctx(old)) if p.migration.id == ID]
    assert sorted(f.key.rsplit(":", 1)[1].split("#")[0] for f in got.findings) == [
        "ddflow_old_next",
        "doc show",
    ]
    assert all(f.path == "AGENTS.md" for f in got.findings)
    assert [(c.path, "docs show" in c.action) for c in got.changes] == [("AGENTS.md", True)]


def test_run_rewrites_backs_up_and_is_idempotent(old: Path) -> None:
    before = (old / "AGENTS.md").read_text()
    log = EventLog(old, "migrator")
    out = M.run(old, log, Config.load(old), [ID])
    assert [(o.migration, o.status) for o in out] == [(ID, "applied")], out
    text = (old / "AGENTS.md").read_text()
    assert "mine: `ddflow doc show`" in text, "the project's own text is not touched"
    assert "Run `ddflow docs show`, then ddflow_next." in text
    assert Managed("rules/work-queue").state(text) == "current"
    assert [p.read_text() for p in Path(out[0].backup).rglob("AGENTS.md")] == [before]
    assert [p.migration.id for p in M.pending(_ctx(old))] == []
    assert M.run(old, log, Config.load(old), [ID]) == []
    assert (old / "AGENTS.md").read_text() == text


def test_a_hand_edited_region_is_left_alone(old: Path) -> None:
    edited = (
        (old / "AGENTS.md").read_text().replace("then ddflow_old_next", "then ddflow_old_next!")
    )
    (old / "AGENTS.md").write_text(edited)
    assert [p for p in M.pending(_ctx(old)) if p.migration.id == ID] == []
    assert M.run(old, EventLog(old, "migrator"), Config.load(old), [ID]) == []
    assert (old / "AGENTS.md").read_text() == edited


def test_without_a_vocabulary_there_is_nothing_to_judge_by(old: Path) -> None:
    R.provide(None)
    assert [p for p in M.pending(_ctx(old)) if p.migration.id == ID] == []


def test_plan_and_apply_act_only_on_what_was_detected(old: Path) -> None:
    """A deprecated name that appears after the detect (so after the backup) is not rewritten,
    and a line inserted above a detected one does not change what it is."""
    m = M.by_id(ID)
    ctx = _ctx(old)
    found = m.detect(ctx)
    other = old / "CLAUDE.md"
    other.write_text(_managed("Run `ddflow doc show` later.\n"))
    assert [c.path for c in m.plan(_ctx(old), found)] == ["AGENTS.md"], "CLAUDE.md is not in it"
    agents = old / "AGENTS.md"
    agents.write_text("a new first line\n" + agents.read_text())  # every line number shifts
    m.apply(_ctx(old), found)
    assert "Run `ddflow docs show`, then ddflow_next." in agents.read_text()
    assert other.read_text() == _managed("Run `ddflow doc show` later.\n")
