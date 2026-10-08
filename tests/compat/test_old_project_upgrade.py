"""New reads old, the upgrade half: `ddflow upgrade --apply` on every released version's
project makes every registered migration verify (B-uni-compat-tests; decision D-compat).

test_new_reads_old.py shows the current code READS each release's project. This shows it can
bring that project forward: each fixture under tests/fixtures/releases/ also gets a rule file
as a pre-rules-in-the-log ddflow left it (the `rules-to-events` migration has something to
do on every release, so the run is not vacuous), `ddflow upgrade --apply migrations` runs
it, and afterwards

- every registered migration finds nothing left to do and its own `verify` reports no problem;
- the run changed only what the migration says: the rule is in the log, the rule file and the
  rest of the project's files are as they were;
- a second run is a no-op (nothing applied, no event appended);
- the whole `--apply` (every category) never fails or leaves a step unable to run, and leaves
  the migrations verified too.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import run_cli
from test_new_reads_old import RELEASES, VERSIONS

from ddflow.api._base import _load
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import migrations as M

RULE_FILE = ".ddflow/rules/r-from-the-old-release.toml"
RULE = (
    'id = "r-from-the-old-release"\n'
    'title = "A rule filed before rules were events"\n'
    "tags = []\n"
    'scope = "project"\n'
    "priority = 50\n\n"
    "Keep the commit message short.\n"
)


@pytest.fixture(params=VERSIONS)
def old(request: pytest.FixtureRequest, tmp_path: Path) -> tuple[str, Path]:
    """The release's project with one rule file added, committed in its own repository."""
    root = tmp_path / "proj"
    shutil.copytree(RELEASES / request.param / "project", root)
    (root / RULE_FILE).parent.mkdir(parents=True, exist_ok=True)
    (root / RULE_FILE).write_text(RULE, encoding="utf-8")
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t.invalid"]
    subprocess.run([*git, "init", "--quiet", "-b", "main"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "--quiet", "--no-verify", "-m", "fixture"], check=True)
    return request.param, root


def _pending(root: Path) -> list[str]:
    log, cfg, _st = _load(root, "fx-upgrader")
    return [p.migration.id for p in M.pending(M.context(root, log, cfg))]


def _unverified(root: Path) -> dict[str, list[str]]:
    """Every registered migration's `verify` problems (only the non-empty ones)."""
    log, cfg, _st = _load(root, "fx-upgrader")
    ctx = M.context(root, log, cfg)
    return {m.id: p for m in M.registry() if (p := list(m.verify(ctx)))}


def _project_files(root: Path) -> dict[str, bytes]:
    """The project's own files, as bytes: not git's, not ddflow's runtime (its log is appended
    to, its index and backups come and go), not `.gitignore`, which any ddflow command may
    extend -- but the project's config and its rule files, which a migration must not touch."""
    out: dict[str, bytes] = {}
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        if not p.is_file() or ".git" in rel.parts or p.name == ".gitignore":
            continue
        if rel.parts[0] == ".ddflow" and rel.as_posix() != ".ddflow/config.toml":
            if rel.parts[1:2] != ("rules",):
                continue
        out[rel.as_posix()] = p.read_bytes()
    return out


def _events(root: Path) -> int:
    return len(EventLog(root, cache_writes=False).read_all())


def _apply(root: Path, *categories: str) -> dict:
    code, out, err = run_cli(root, "--json", "upgrade", "--apply", *categories, agent="fx-upgrader")
    assert "Traceback" not in err, err
    body = json.loads(out)
    return {"exit": code, **body["applied"]}


def test_the_fixture_gives_the_migration_something_to_do(old: tuple[str, Path]) -> None:
    """Without this the tests below would pass on a project nothing applies to."""
    _, root = old
    assert "rules-to-events" in _pending(root)


def test_apply_migrations_makes_every_migration_verify(old: tuple[str, Path]) -> None:
    version, root = old
    before = _project_files(root)

    done = _apply(root, "migrations")

    assert done["exit"] == 0, done
    assert (done["failed"], done["unavailable"], done["refused"]) == (0, 0, 0), done
    statuses = {r["id"]: r["status"] for r in done["results"]}
    assert statuses == {"migration:rules-to-events": "applied"}, (version, statuses)
    assert _pending(root) == [], "a fresh detect finds nothing left"
    assert _unverified(root) == {}, "and every migration's verify is clean"
    _, _, st = _load(root, "fx-upgrader")
    assert "rule:r-from-the-old-release" in st.defs, "the rule is now a record in the log"
    after = _project_files(root)
    changed = sorted(n for n in before.keys() & after.keys() if before[n] != after[n])
    added, removed = sorted(after.keys() - before.keys()), sorted(before.keys() - after.keys())
    assert (changed, added, removed) == ([], [], []), (
        f"the migration only appends events: changed={changed} added={added} removed={removed}"
    )


def test_a_second_apply_is_a_no_op(old: tuple[str, Path]) -> None:
    _, root = old
    assert _apply(root, "migrations")["exit"] == 0
    events = _events(root)

    again = _apply(root, "migrations")

    assert again["exit"] == 0 and again["applied"] == 0, again
    assert _events(root) == events, "nothing appended the second time"


def test_the_whole_apply_fails_nothing_and_leaves_the_migrations_verified(
    old: tuple[str, Path],
) -> None:
    version, root = old
    done = _apply(root)

    # exit 3 is an item that waits for the operator's `--confirm` (an old release's
    # hand-set value): refused is not failed, and nothing may have FAILED or been unable to run.
    assert done["exit"] in (0, 3), (version, done)
    assert done["failed"] == 0 and done["unavailable"] == 0, (version, done)
    assert _pending(root) == [] and _unverified(root) == {}
    fold(EventLog(root, cache_writes=False).read_all(), strict=True)  # still folds strictly
    code, out, err = run_cli(root, "status", agent="fx-upgrader")
    assert code in (0, 2) and "Traceback" not in err and out.strip(), (code, out, err)


def test_the_snapshot_helper_keeps_what_a_migration_must_not_touch(tmp_path: Path) -> None:
    """The guard above is only as good as its filter: pin what it includes and excludes."""
    for rel in (
        "README.md",
        "src/a.py",
        ".ddflow/config.toml",
        ".ddflow/rules/r-x.toml",
        ".ddflow/events/x.jsonl",
        ".ddflow/backups/b/manifest.json",
        ".ddflow/.gitignore",
        ".gitignore",
        ".git/HEAD",
    ):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x")
    (tmp_path / RULE_FILE).write_text(RULE)
    assert sorted(_project_files(tmp_path)) == sorted(
        ["README.md", "src/a.py", ".ddflow/config.toml", ".ddflow/rules/r-x.toml", RULE_FILE]
    )
