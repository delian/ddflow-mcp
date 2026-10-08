"""B-uni-compat-artifacts.2-adopt: adopt's managed blocks and command files use `fsio.Managed`.

The rules block in AGENTS.md / CLAUDE.md and the `/implement` command file carry the stamped
D-doc-regions grammar (`ddflow:begin <name> ddflow=V fmt=N sha=H`), so an older, newer or
hand-edited copy is told apart from a current one; the markers older ddflow wrote are still
read and are migrated when the block is next written; a block a NEWER ddflow wrote is refused
(exit 3) instead of downgraded; and `adopt --refresh-docs` saves the originals first.
"""

from __future__ import annotations

import re
from pathlib import Path

from conftest import run_cli

from ddflow import FORMAT_LEVEL
from ddflow.services import adopt as A
from ddflow.services.backups import BACKUPS

BEGIN = "ddflow:begin rules/work-queue"
CMD = ".claude/commands/implement.md"


def _adopted(repo: Path, agents: str = "claude") -> None:
    assert run_cli(repo, "init")[0] == 0
    assert run_cli(repo, "adopt", "--agents", agents)[0] == 0


def _backups(repo: Path) -> list[Path]:
    root = repo / BACKUPS
    return sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []


def test_the_written_block_is_stamped_and_reads_back_current(repo: Path) -> None:
    _adopted(repo)

    text = (repo / "AGENTS.md").read_text()

    stamp = A.BLOCK.stamp(text)
    assert stamp is not None and stamp.fmt == FORMAT_LEVEL
    assert A.BLOCK.state(text) == "current"
    assert A.block_body(text).strip() == A.project_body().strip()
    assert all(r.state == A.CURRENT for r in A.rules_status(repo))


def test_a_block_under_the_legacy_markers_is_stale_and_migrates_keeping_the_prose(
    repo: Path,
) -> None:
    _adopted(repo)
    legacy = f"# Mine\n\nkeep me\n\n{A.LEGACY_BEGIN}\n{A.project_body()}{A.LEGACY_END}\n\ntail\n"
    (repo / "AGENTS.md").write_text(legacy)
    assert {r.path: r.state for r in A.rules_status(repo)}["AGENTS.md"] == A.STALE

    assert run_cli(repo, "adopt", "--refresh-docs")[0] == 0

    text = (repo / "AGENTS.md").read_text()
    assert A.LEGACY_BEGIN not in text and A.LEGACY_END not in text
    assert text.count(BEGIN) == 1 and A.BLOCK.state(text) == "current"
    assert "keep me" in text and text.rstrip().endswith("tail")
    assert {r.path: r.state for r in A.rules_status(repo)}["AGENTS.md"] == A.CURRENT


def test_a_hand_edit_inside_the_block_is_seen_as_edited_and_saved_before_it_is_replaced(
    repo: Path,
) -> None:
    _adopted(repo)
    path = repo / "AGENTS.md"
    edited = path.read_text().replace("Claim before you edit", "Claim before you EDIT-ish")
    path.write_text(edited)
    assert A.BLOCK.state(edited) == "edited"

    code, out, _ = run_cli(repo, "adopt", "--refresh-docs")

    assert code == 0 and "saved the originals in" in out, out
    assert A.BLOCK.state(path.read_text()) == "current"
    saved = _backups(repo)
    assert len(saved) == 1 and saved[0].name.endswith("-refresh-docs")
    assert (saved[0] / "files" / "in" / "AGENTS.md").read_text() == edited


def test_a_noop_refresh_leaves_no_backup(repo: Path) -> None:
    _adopted(repo)

    code, out, _ = run_cli(repo, "adopt", "--refresh-docs")

    assert code == 0 and "saved the originals" not in out
    assert _backups(repo) == []


def test_a_stale_driver_doc_is_saved_byte_for_byte_before_the_refresh(repo: Path) -> None:
    _adopted(repo)
    driver = repo / "docs/ddflow/drivers/implement-phase.md"
    driver.write_text("an older driver\n")

    assert run_cli(repo, "adopt", "--refresh-docs")[0] == 0

    (saved,) = _backups(repo)
    assert (
        saved / "files" / "in" / "docs/ddflow/drivers/implement-phase.md"
    ).read_text() == "an older driver\n"
    assert driver.read_text() != "an older driver\n"


def test_a_block_a_newer_ddflow_wrote_is_refused_not_downgraded(repo: Path) -> None:
    _adopted(repo)
    path = repo / "AGENTS.md"
    newer = re.sub(r"fmt=\d+", f"fmt={FORMAT_LEVEL + 1}", path.read_text(), count=1)
    path.write_text(newer)

    code, out, err = run_cli(repo, "adopt", "--refresh-docs")

    assert code == 3, out + err
    assert "upgrade ddflow to >=" in out + err
    assert path.read_text() == newer


def test_a_block_with_broken_markers_is_left_alone_and_said(repo: Path) -> None:
    _adopted(repo)
    path = repo / "AGENTS.md"
    broken = path.read_text().replace("<!-- ddflow:end rules/work-queue -->", "")
    path.write_text(broken)

    code, out, err = run_cli(repo, "adopt", "--refresh-docs")

    assert code == 3 and "markers" in out + err
    assert path.read_text() == broken


def test_both_grammars_are_block_markers() -> None:
    assert A.block_marker(A.LEGACY_BEGIN) == "begin" and A.block_marker(A.LEGACY_END) == "end"
    stamped = A.BLOCK.render("x\n").splitlines()
    assert A.block_marker(stamped[0]) == "begin" and A.block_marker(stamped[-1]) == "end"
    assert A.block_marker("some ordinary line") == ""


def test_the_command_file_is_a_stamped_region_after_its_front_matter(repo: Path) -> None:
    _adopted(repo)

    text = (repo / CMD).read_text()

    assert text.startswith("---\n"), "the front matter must stay first"
    _head, _, rest = text.partition("\n---\n")
    assert "ddflow:begin commands/implement" in rest.splitlines()[0]
    assert text.rstrip().endswith("<!-- ddflow:end commands/implement -->")
    assert A.MANAGED_MARK not in text
    assert run_cli(repo, "adopt", "--agents", "claude")[0] == 0
    assert (repo / CMD).read_text() == text, "a second adopt must not change the file"


def test_a_command_file_with_the_legacy_mark_is_migrated(repo: Path) -> None:
    _adopted(repo)
    (repo / CMD).write_text(f"---\ndescription: old\n---\n{A.MANAGED_MARK} — old -->\n\nold body\n")

    assert run_cli(repo, "adopt", "--agents", "claude")[0] == 0

    text = (repo / CMD).read_text()
    assert A.MANAGED_MARK not in text and "ddflow:begin commands/implement" in text
    assert "old body" not in text


def test_a_command_file_that_is_the_projects_own_is_kept(repo: Path) -> None:
    _adopted(repo)
    (repo / CMD).write_text("the project's own\n")

    code, out, _ = run_cli(repo, "adopt", "--agents", "claude")

    assert code == 0 and "kept .claude/commands/implement.md" in out
    assert (repo / CMD).read_text() == "the project's own\n"


def test_deleting_the_begin_line_keeps_a_command_file_as_the_projects_own(repo: Path) -> None:
    _adopted(repo)
    path = repo / CMD
    lines = path.read_text().splitlines(keepends=True)
    mine = "".join(ln for ln in lines if "ddflow:begin commands/implement" not in ln)
    path.write_text(mine)

    code, out, _ = run_cli(repo, "adopt", "--agents", "claude")

    assert code == 0 and "kept .claude/commands/implement.md" in out
    assert path.read_text() == mine


def test_a_command_file_a_newer_ddflow_wrote_is_refused(repo: Path) -> None:
    _adopted(repo)
    path = repo / CMD
    newer = re.sub(r"fmt=\d+", f"fmt={FORMAT_LEVEL + 1}", path.read_text(), count=1)
    path.write_text(newer.replace("Run `/loop`", "Run `/loop2`"))

    code, out, err = run_cli(repo, "adopt", "--agents", "claude")

    assert code != 0 and "upgrade ddflow to >=" in out + err
    assert "Run `/loop2`" in path.read_text()


def test_a_deleted_rules_file_is_recreated_without_a_backup_of_nothing(repo: Path) -> None:
    _adopted(repo)
    (repo / "AGENTS.md").unlink()

    code, out, _ = run_cli(repo, "adopt", "--refresh-docs")

    assert code == 0 and (repo / "AGENTS.md").is_file()
    assert "saved the originals" not in out and _backups(repo) == []


def test_a_project_file_named_like_the_manifest_does_not_overwrite_it(tmp_path: Path) -> None:
    from ddflow.services.backups import MANIFEST, make_backup

    (tmp_path / "manifest.json").write_text("mine\n")

    dest = make_backup(tmp_path, [tmp_path / "manifest.json"], "a", "b")

    assert (dest / "files" / "in" / "manifest.json").read_text() == "mine\n"
    assert '"existed": true' in (dest / MANIFEST).read_text()


def test_a_command_file_whose_end_marker_is_gone_is_the_projects_own(repo: Path) -> None:
    _adopted(repo)
    path = repo / CMD
    mine = path.read_text().replace("<!-- ddflow:end commands/implement -->", "")
    path.write_text(mine)

    code, out, _ = run_cli(repo, "adopt", "--agents", "claude")

    assert code == 0 and "kept .claude/commands/implement.md" in out
    assert path.read_text() == mine


def test_inside_and_outside_files_never_share_a_backup_path(tmp_path: Path) -> None:
    from ddflow.services.backups import make_backup

    inside = tmp_path / "proj" / "_outside" / "notes.txt"
    inside.parent.mkdir(parents=True)
    inside.write_text("inside\n")
    other = tmp_path / "notes.txt"
    other.write_text("outside\n")

    dest = make_backup(tmp_path / "proj", [inside, other], "a", "b")

    assert (dest / "files" / "in" / "_outside" / "notes.txt").read_text() == "inside\n"
    assert (dest / "files" / "out" / other.as_posix().lstrip("/")).read_text() == "outside\n"


def test_reversed_legacy_markers_are_a_broken_block_not_an_empty_one() -> None:
    import pytest

    from ddflow.infra.fsio import RegionError

    text = f"{A.LEGACY_END}\nnotes\n{A.LEGACY_BEGIN}\n"

    assert A.has_legacy_block(text) is False
    with pytest.raises(RegionError):
        A.block_body(text)
    assert A._block_state(text, "want") == A.NO_BLOCK


def test_an_empty_front_matter_block_stays_first() -> None:
    assert A._split_front_matter("---\n---\nbody\n") == ("---\n---\n", "body\n")
    assert A._split_front_matter("---\na: 1\n---\nbody\n") == ("---\na: 1\n---\n", "body\n")
    assert A._split_front_matter("no front matter\n") == ("", "no front matter\n")


def test_adopt_refuses_a_file_with_reversed_legacy_markers_and_changes_nothing(repo: Path) -> None:
    _adopted(repo)
    path = repo / "AGENTS.md"
    reversed_ = f"{A.LEGACY_END}\nnotes\n{A.LEGACY_BEGIN}\n"
    path.write_text(reversed_)

    code, out, err = run_cli(repo, "adopt", "--refresh-docs")

    assert code == 3 and "reversed" in out + err
    assert path.read_text() == reversed_
