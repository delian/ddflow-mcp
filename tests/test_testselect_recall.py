"""B4067086b6b / D-test-economy-r2: a precise selection must keep recall.

Replays the last few merges of THIS repository from git history. For each, the tests the
merge changed alongside its source are the ones its author had to touch -- a regression
test, or a test the change broke -- and the selection computed from the SOURCE change alone
(over the tree as it was at the merge) must hold them, bounded by the project's defaults.
Two floors: nothing the earlier, unbounded selector found is lost, and the absolute recall
stays high. A merge whose answer is the whole suite (a hub changed) has perfect recall.

Bounded (`MERGES` replays, no network) and skipped when git history is not there: a
shallow clone, a tarball, or `DDFLOW_SKIP_HISTORY_REPLAY=1`.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from helpers import git_proc, git_raw

from ddflow.config import Config
from ddflow.services import testidents as TI
from ddflow.services import testscope as TSC
from ddflow.services import testselect as T

ROOT = Path(__file__).resolve().parents[1]
MERGES = 4  #: replays; each parses the tree of its commit, about ten seconds
SCAN = 40  #: merges looked at to find that many with both source and tests in them
ABSOLUTE_FLOOR = 0.7  #: share of co-changed tests the selection holds, over all replays


def _merges() -> list[str]:
    if os.environ.get("DDFLOW_SKIP_HISTORY_REPLAY"):
        pytest.skip("DDFLOW_SKIP_HISTORY_REPLAY is set")
    probe = git_proc(ROOT, "rev-parse", "--is-shallow-repository")
    if probe.returncode != 0 or probe.stdout.strip() != "false":
        pytest.skip("no full git history here")
    log = git_proc(
        ROOT, "log", "--first-parent", "--merges", "--grep=^merge ", "-n", str(SCAN), "--format=%H"
    )
    shas = log.stdout.split() if log.returncode == 0 else []
    if len(shas) < 3:
        pytest.skip("fewer than three merges in this history")
    return shas


def _replay(sha: str) -> tuple[list[str], list[str]] | None:
    """(source files, test files) a merge changed, or None when it has no source or no test
    change to replay."""
    names = git_raw(ROOT, "diff", "--name-only", "--no-renames", f"{sha}^1", sha).splitlines()
    names = [n for n in names if n and not n.startswith(".ddflow/")]
    src = [n for n in names if not T.is_test_file(n)]
    co = [n for n in names if T.is_test_file(n)]
    return (src, co) if src and co else None


@contextlib.contextmanager
def _checkout(sha: str):
    """The tree of ``sha`` extracted to a scratch directory, and its file list."""
    raw = Path(tempfile.mkdtemp())
    data = subprocess.run(["git", "-C", str(ROOT), "archive", sha], capture_output=True, check=True)
    try:
        with tarfile.open(fileobj=io.BytesIO(data.stdout)) as tar:
            tar.extractall(raw, filter="data")
        yield raw, git_raw(ROOT, "ls-tree", "-r", "--name-only", sha).split()
    finally:
        shutil.rmtree(raw, ignore_errors=True)


def _replayed(sha: str, src: list[str], co: list[str]) -> tuple[int, int, list[str]]:
    """(co-changed tests that still exist, how many the selection holds, the ones it lost
    that the earlier selector -- no hubs, no budget, no identifiers -- had held)."""
    with _checkout(sha) as (raw, listed):
        py = [f for f in src if f.endswith(".py")]
        idents = TI.changed_identifiers(ROOT, f"{sha}^1", py, tip=sha, source=raw)
        new = T.select(raw, "x", changed=src, listed=listed, idents=idents, **TSC.limits(Config()))
        co = [c for c in co if (raw / c).exists()]
        assert new is not None
        if new.full:
            return len(co), len(co), []
        got = {t.path for t in new.tests}
        missed = [c for c in co if c not in got]
        if not missed:
            return len(co), len(co), []
        old = T.select(raw, "x", changed=src, listed=listed, idents={})
        assert old is not None
        before = {t.path for t in old.tests}
        return len(co), len(co) - len(missed), [f"{sha[:8]} {c}" for c in missed if c in before]


def test_the_selection_holds_the_tests_recent_merges_changed_with_their_source():
    held = total = replayed = 0
    lost: list[str] = []
    for sha in _merges():
        rows = _replay(sha)
        if rows is None:
            continue
        n, kept, gone = _replayed(sha, *rows)
        total, held, replayed = total + n, held + kept, replayed + 1
        lost += gone
        if replayed >= MERGES:
            break
    if replayed < 2:
        pytest.skip("too few replayable merges in this history")
    assert not lost, f"the precise selection lost tests the earlier selector held: {lost}"
    assert held / total >= ABSOLUTE_FLOOR, f"recall {held}/{total} over {replayed} merges"
