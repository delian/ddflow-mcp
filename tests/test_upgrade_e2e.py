"""B-upgrade.7-docs-e2e: an old project goes through plan -> apply -> doctor -> replay.

The old projects are the committed fixtures a real release built (tests/fixtures/releases/<ver>/
project, populated logs). Everything runs on throwaway tmp dirs through the CLI, as an operator
would. A fresh `init` is the up-to-date control: its plan is empty. The documentation the
upgrade needs is asserted here too, and none of it may name a repository of this project's own.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import run_cli

ROOT = Path(__file__).resolve().parents[1]
RELEASES = ROOT / "tests" / "fixtures" / "releases"
VERSIONS = sorted(p.name for p in RELEASES.iterdir() if (p / "meta.json").is_file())
AGENT = "e2e-upgrader"


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t.invalid", *args],
        check=True,
        capture_output=True,
    )


def _json(out: str) -> dict:
    return json.JSONDecoder().raw_decode(out)[0]


@pytest.fixture(params=VERSIONS)
def old(request: pytest.FixtureRequest, tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    shutil.copytree(RELEASES / request.param / "project", root)
    _git(root, "init", "--quiet", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "--quiet", "--no-verify", "-m", "fixture")
    return root


def test_old_project_upgrades_end_to_end(old: Path) -> None:
    code, out, err = run_cli(old, "upgrade", "--plan", agent=AGENT)
    assert code == 1 and "Traceback" not in err, (code, out, err)
    assert "upgrade item(s) pending" in out

    code, out, err = run_cli(old, "--json", "upgrade", "--apply", agent=AGENT)
    assert code in (0, 3) and "Traceback" not in err, (code, out, err)
    applied = _json(out)["applied"]
    assert applied["failed"] == 0 and applied["unavailable"] == 0, applied
    if code == 3:  # a value the old release's operator set: only the operator accepts it
        keys = [r["key"] for r in applied["results"] if r["status"] == "refused"]
        assert keys
        flags = [f for k in keys for f in ("--confirm", k)]
        code, out, err = run_cli(
            old, "upgrade", "--apply", *flags, "--reason", "e2e: accepted", agent=AGENT
        )
        assert code == 0, (code, out, err)

    code, out, err = run_cli(old, "upgrade", "--plan", agent=AGENT)
    assert code == 0 and "Up to date" in out, (code, out, err)

    code, out, err = run_cli(old, "doctor", agent=AGENT)
    assert code == 0 and "Healthy." in out, (code, out, err)

    code, out, err = run_cli(old, "replay", "--verify", agent=AGENT)
    assert code == 0 and "Traceback" not in err, (code, out, err)

    code, out, err = run_cli(old, "upgrade", "--apply", agent=AGENT)  # idempotent
    assert code == 0, (code, out, err)


def test_fresh_init_has_an_empty_plan(tmp_path: Path) -> None:
    _git(tmp_path, "init", "--quiet", "-b", "main")
    code, out, err = run_cli(tmp_path, "init", agent=AGENT)
    assert code == 0, (code, out, err)
    code, out, err = run_cli(tmp_path, "upgrade", "--plan", agent=AGENT)
    assert code == 0 and "nothing to upgrade" in out, (code, out, err)


def _section(text: str, heading: str) -> str:
    m = re.search(rf"^### {re.escape(heading)}\n(.*?)(?=^#{{1,3}} )", text, re.S | re.M)
    assert m, f"README has no section {heading!r}"
    return m.group(1)


def test_readme_explains_the_upgrade_and_both_backup_options() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    whole = _section(readme, "Upgrading a project: the whole picture")
    for needle in ("--plan", "--apply", "--confirm", "--restore", "git revert", "merge=union"):
        assert needle in whole, needle
    applying = _section(readme, "Applying an upgrade")
    assert "Backups: local or snapshot" in applying
    for needle in ("`local` (default)", "`snapshot`", "--snapshot", "backup_keep", "--restore"):
        assert needle in applying, needle


def test_recovery_doc_points_at_the_upgrade_path() -> None:
    text = (ROOT / "docs" / "RECOVERY.md").read_text(encoding="utf-8")
    assert "no migration path" not in text
    assert "ddflow upgrade --apply" in text


def test_upgrade_docs_and_templates_name_no_repository_of_this_project() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    texts = [
        _section(readme, "Upgrading a project: the whole picture"),
        _section(readme, "Applying an upgrade"),
        (ROOT / "docs" / "RECOVERY.md").read_text(encoding="utf-8"),
    ]
    texts += [
        p.read_text(encoding="utf-8") for p in (ROOT / "ddflow/templates/upgrade").rglob("*.toml")
    ]
    texts += [(ROOT / "ddflow/templates/drivers/implement-phase.md").read_text(encoding="utf-8")]
    pattern = re.compile(r"run_nemo_run|home-simulator|home_simulator|/home/\w+/src", re.I)
    for t in texts:
        assert not pattern.search(t), pattern.search(t)
