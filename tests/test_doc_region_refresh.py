"""B-tpl-refresh (D-doc-regions): refresh, doctor, the pre-commit check and docscheck
handle named-region documents."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git_proc as _git

from ddflow.config import Config
from ddflow.services import docscheck
from ddflow.services.export import refresh as RF
from ddflow.services.export import region_check as RC
from ddflow.services.export import regions as RG

TEMPLATE = (
    "# Plan\n\nmy intro\n\n"
    '{% call region("sum") %}\n'
    "{% for l in lanes %}{% for p in l.phases %}phase {{ p.id }}\n{% endfor %}{% endfor %}"
    "{% endcall %}\n"
)
DOC = "PLAN.md"


@pytest.fixture
def proj(repo: Path) -> Path:
    run_cli(repo, "adopt", "--agents", "claude")
    (repo / ".ddflow" / "config.toml").write_text(
        '[enforce]\ncommit_without_lease = "off"\n[export]\ndocuments = ["roadmap"]\n'
        'refresh = "merge"\n[export.roadmap]\npath = "PLAN.md"\nmode = "region"\n'
        'template = "plan.md.j2"\n'
    )
    (repo / "plan.md.j2").write_text(TEMPLATE)
    run_cli(repo, "phase", "add", "P1", "--title", "Core")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "scaffold", "--no-verify")
    assert run_cli(repo, "export", "roadmap", "--update", "--yes")[0] == 0
    return repo


def _commit(repo: Path, *paths: str) -> subprocess.CompletedProcess:
    _git(repo, "add", *paths)
    return subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "docs"],
        capture_output=True,
        text=True,
        env={**os.environ},
        timeout=180,
    )


def _move_the_log(repo: Path) -> None:
    assert run_cli(repo, "phase", "add", "P2", "--title", "More")[0] == 0


def _refresh(repo: Path):
    return RF.refresh_selected(repo, "merge", cfg=Config.load(repo))


def test_refresh_splices_a_stale_region_and_keeps_user_text(proj):
    p = proj / DOC
    p.write_text(p.read_text() + "\nmy outro\n")
    _move_the_log(proj)
    out = _refresh(proj)
    assert [o.action for o in out.outcomes] == ["updated"], out.summary()
    text = p.read_text()
    assert "phase P2" in text and "my intro" in text and "my outro" in text
    assert _refresh(proj).outcomes[0].action == "unchanged"


def test_refresh_skips_a_hand_edited_region(proj):
    p = proj / DOC
    p.write_text(p.read_text().replace("phase P1", "phase mine"))
    _move_the_log(proj)
    o = _refresh(proj).outcomes[0]
    assert o.action == "skipped" and "hand-edited" in o.message
    assert "phase mine" in p.read_text()


def test_refresh_still_skips_an_append_document(proj):
    cfg = proj / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text().replace('mode = "region"', 'mode = "append"'))
    o = _refresh(proj).outcomes[0]
    assert o.action in ("skipped", "failed")


def test_doctor_reports_a_stale_and_a_hand_edited_region(proj):
    assert "(roadmap) is" not in run_cli(proj, "doctor")[1]
    _move_the_log(proj)
    assert any("PLAN.md (roadmap) is stale" in ln for ln in run_cli(proj, "doctor")[1].splitlines())
    p = proj / DOC
    p.write_text(p.read_text().replace("phase P1", "phase mine"))
    assert "PLAN.md (roadmap) is hand-edited" in run_cli(proj, "doctor")[1]


def test_precommit_refuses_a_staged_region_whose_body_was_edited(proj):
    p = proj / DOC
    p.write_text(p.read_text().replace("phase P1", "phase mine"))
    r = _commit(proj, DOC)
    assert r.returncode != 0
    assert DOC in r.stderr and "roadmap/sum was edited by hand" in r.stderr


def test_precommit_passes_a_region_with_user_text_changed_outside_it(proj):
    p = proj / DOC
    p.write_text(p.read_text().replace("my intro", "a new intro"))
    r = _commit(proj, DOC)
    assert r.returncode == 0, r.stderr


def test_precommit_names_a_file_whose_markers_cannot_be_read(proj):
    (proj / "BAD.md").write_text("<!-- ddflow:begin d/a sha=000000000000 -->\nx\n")
    r = _commit(proj, "BAD.md")
    assert r.returncode != 0 and "BAD.md" in r.stderr and "never closed" in r.stderr


def test_region_check_edited_and_mask():
    ok = RG.render_region("d", "a", "A\n")
    bad = ok.replace("A\n", "B\nC\n")
    assert RC.edited("x\n" + ok) == [] and RC.edited(bad) == ["d/a"]
    masked = RC.mask("top\n" + ok + "bottom\n")
    assert "A" not in masked and masked.count("\n") == ("top\n" + ok + "bottom\n").count("\n")
    assert masked.startswith("top\n<!-- ddflow:begin") and masked.endswith("bottom\n")
    broken = "<!-- ddflow:begin d/a sha=000000000000 -->\nx\n"
    assert RC.mask(broken) == broken and "never closed" in RC.problem(broken)


def test_docscheck_ignores_text_inside_a_generated_region(proj):
    body = "The `no_such_identifier_zz` thing.\n"
    (proj / "GUIDE.md").write_text(
        "# Guide\n\n" + RG.render_region("d", "a", body) + "\nHand `another_missing_name_zz`.\n"
    )
    _git(proj, "add", "GUIDE.md")
    _git(proj, "commit", "-qm", "guide", "--no-verify")
    rep = docscheck.check_docs(proj, docs=["GUIDE.md"], commands=[])
    refs = {f.ref for f in rep.findings}
    assert "another_missing_name_zz" in refs and "no_such_identifier_zz" not in refs


def test_adopts_stamped_blocks_are_not_read_as_regions():
    stamped = (
        "<!-- ddflow:begin rules/work-queue ddflow=0.2.0 fmt=1 sha=f60922c1c1f6 -->\nx\n"
        "<!-- ddflow:end rules/work-queue -->\n"
    )
    assert RC.problem(stamped) == "" and RC.edited(stamped) == [] and RC.mask(stamped) == stamped


def test_an_orphaned_end_marker_is_a_problem_not_silence():
    orphan = "top\nbody\n<!-- ddflow:end d/a -->\n"
    assert RC.problem(orphan) and RC.mask(orphan) == orphan
