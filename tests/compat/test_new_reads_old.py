"""New reads old: the current ddflow reads every released version's project the way that
release did (B-uni-compat-tests; decision D-compat).

Each directory under tests/fixtures/releases/ is a small project a RELEASED ddflow built and
committed (`scripts/ci/compat-matrix build`), with `expected.json`: what that release folded
its own log into. The current code must

- fold the log strictly -- every event kind still understood, none skipped;
- agree with the old release on every field the old release had (fields added since are not
  compared: tests/compat/projection.py walks the expected side only);
- get the same state from a cold replay of the shard bytes as from the live read path;
- answer status, brief, show, board, replay and doctor on the project without crashing.

A disagreement is a compatibility break: file it as a bug and fix it. It is never fixed by
regenerating the fixture -- that would make the old release's answer whatever the new code
says. The one exception is the old release being WRONG: a fold bug fixed since. Each such
difference is declared in FOLD_FIXES with the bug that fixed it, and a declared difference
that no longer appears fails too, so the list cannot outlive what it excuses.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import run_cli
from projection import differences, plain, project

from ddflow.core.events import Event
from ddflow.core.model import fold
from ddflow.infra.log import EventLog

RELEASES = Path(__file__).resolve().parents[1] / "fixtures" / "releases"
VERSIONS = sorted(
    (p.name for p in RELEASES.iterdir() if (p / "meta.json").is_file()),
    # a dev or pre-release name ("0.2.0.dev1") sorts after the numbers, never fails collection
    key=lambda v: tuple((0, int(x), "") if x.isdigit() else (1, 0, x) for x in v.split(".")),
)


#: (version, difference path) -> the fold bug, fixed since that release, that makes the
#: current reading differ from the old one. Nothing else may differ.
FOLD_FIXES: dict[tuple[str, str], str] = {
    # The scenario's T3 is claimed, started and released. Before 0.1.13 the release left it
    # RUNNING with no lease, holding a place nobody could claim; fixed in 4d98c116.
    ("0.1.3", "items.T3.state"): "B601fa7eff9: release hands a started item back to the queue",
    ("0.1.10", "items.T3.state"): "B601fa7eff9: release hands a started item back to the queue",
}


def test_the_matrix_has_its_releases() -> None:
    """A fixture directory that silently went missing would make the matrix pass vacuously."""
    assert {"0.1.3", "0.1.10", "0.1.15", "0.1.18"} <= set(VERSIONS)


@pytest.fixture(params=VERSIONS)
def release(request: pytest.FixtureRequest, tmp_path: Path) -> tuple[str, Path]:
    """The release's project, copied out of the source tree and committed in its own git
    repository, so nothing a reader writes lands in the fixture."""
    version = request.param
    root = tmp_path / "proj"
    shutil.copytree(RELEASES / version / "project", root)
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t.invalid"]
    subprocess.run([*git, "init", "--quiet", "-b", "main"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "--quiet", "--no-verify", "-m", "fixture"], check=True)
    return version, root


def _cold_replay(root: Path) -> list[Event]:
    """Every shard line parsed from scratch, sorted, first of each id kept -- the definition,
    independent of the read path's caches and snapshots."""
    events = [
        Event.from_json(line)
        for path in sorted((root / ".ddflow" / "events").glob("*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    seen: set[str] = set()
    out = []
    for e in sorted(events, key=Event.sort_key):
        if e.id not in seen:
            seen.add(e.id)
            out.append(e)
    return out


def test_new_folds_what_the_old_release_folded(release: tuple[str, Path]) -> None:
    version, root = release
    expected = json.loads((RELEASES / version / "expected.json").read_text(encoding="utf-8"))
    assert expected.pop("version") == version
    found = {d.split(":", 1)[0]: d for d in differences(expected, project(root))}
    declared = {path for v, path in FOLD_FIXES if v == version}
    assert {p: d for p, d in found.items() if p not in declared} == {}
    assert declared <= set(found), f"declared fold fixes no longer differ: {declared - set(found)}"


def test_replay_equals_live(release: tuple[str, Path]) -> None:
    _, root = release
    live = fold(EventLog(root, cache_writes=False).read_all(), strict=True)
    cold = fold(_cold_replay(root), strict=True)
    assert not live.skipped_kinds
    assert plain(live) == plain(cold)


@pytest.mark.parametrize(
    "argv",
    [
        ("status",),
        ("brief",),
        ("show", "T1"),
        ("board",),
        ("replay",),
        ("bug", "list", "--all"),
        ("decision", "list"),
        ("doctor",),
    ],
    ids="-".join,
)
def test_current_cli_reads_the_old_project(
    release: tuple[str, Path], argv: tuple[str, ...]
) -> None:
    _, root = release
    code, out, err = run_cli(root, *argv, agent="fx-reader")
    assert "Traceback" not in err, err
    # doctor's exit 1 is a finding about the project, not a failure to read it.
    assert code in ((0, 1, 2) if argv[0] == "doctor" else (0, 2)), (code, out, err)
    assert out.strip(), err
