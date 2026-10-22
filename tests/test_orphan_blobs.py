"""Event blobs no shard names are nobody's: not committed, and `cleanup` lists them
(B-uncommitted-blobs-owner-unknown).

`commit_shards` committed every untracked file under `.ddflow/blobs/`, so a blob written by
an agent that never logged the event naming it (a crash, a refused append) was committed
under whichever agent next reached a door, and one left on disk could not be told from a
live agent's. A blob now travels only with a shard that names it, and the rest are reported.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow import api
from ddflow.core import blobref as B
from ddflow.infra.blobstore import BlobStore
from ddflow.services import eventcommit as EC


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )
    return done.stdout


def _blob(repo: Path, value: str) -> str:
    raw = B.body(value)
    ref = B.address(raw)
    BlobStore(repo).put(ref, raw)
    return ref


def _named(repo: Path, ref: str, shard: str = "mine") -> None:
    line = json.dumps({"kind": "x", "evidence": {"k": {B.KEY: ref, "bytes": 5}}})
    (repo / EC.EVENTS / f"{shard}.jsonl").write_text(line + "\n")


def _tracked(repo: Path) -> set[str]:
    return set(_git(repo, "ls-files", EC.BLOBS).split())


def test_commit_takes_a_blob_its_shard_names_and_leaves_an_orphan(repo):
    run_cli(repo, "init")
    named, orphan = _blob(repo, "named"), _blob(repo, "orphan")
    _named(repo, named)
    sha, note = EC.commit_shards(repo, "test")
    assert sha, note
    tracked = _tracked(repo)
    assert str(BlobStore(repo).path(named).relative_to(repo)) in tracked
    assert str(BlobStore(repo).path(orphan).relative_to(repo)) not in tracked


def test_orphan_blobs_lists_what_no_shard_names(repo):
    run_cli(repo, "init")
    named, orphan = _blob(repo, "named"), _blob(repo, "orphan")
    _named(repo, named)
    assert EC.orphan_blobs(repo) == [str(BlobStore(repo).path(orphan).relative_to(repo))]


def test_cleanup_reports_orphan_blobs(repo):
    run_cli(repo, "init")
    orphan = _blob(repo, "orphan")
    out = api.cleanup(repo)
    assert out.data["orphan_blobs"] == [str(BlobStore(repo).path(orphan).relative_to(repo))]


def test_doctor_does_not_count_an_orphan_blob_as_a_loose_shard(repo):
    from ddflow.api.reporting.health import _loose_shards

    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    _blob(repo, "orphan")
    assert _loose_shards(repo) == []


def test_only_orphans_left_is_not_reported_as_an_uncommitted_log(repo):
    run_cli(repo, "init")
    EC.commit_shards(repo, "adopt")
    _blob(repo, "orphan")
    sha, note = EC.commit_shards(repo, "again")
    assert not sha
    assert "no event names" in note and "event log not committed" not in note
