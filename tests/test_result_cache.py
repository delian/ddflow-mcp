"""Content-addressed gate result cache (RC-tree-result-cache): reuse a ci or unit_tests result
for the same code tree, command and environment; per project, bounded, off switch."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from ddflow.config import Config
from ddflow.services import resultcache as RC
from ddflow.services.gates import runner as GR
from ddflow.services.gates.defs import GateDef


def git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    return subprocess.run(
        [
            "git",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-C",
            str(repo),
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout


def project(tmp_path: Path, name: str = "p") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.py").write_text("x = 1\n")
    (repo / "docs").mkdir()
    (repo / "docs" / "n.md").write_text("notes\n")
    (repo / ".ddflow").mkdir()
    (repo / ".ddflow" / "events").mkdir()
    (repo / ".ddflow" / "events" / "e.jsonl").write_text("{}\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo


def commit(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", f"edit {rel}")


def cfg(**kw) -> Config:
    c = Config()
    for k, v in kw.items():
        setattr(c.resultcache, k, v)
    return c


def key(repo: Path, command="pytest tests/ -q", gate="unit_tests", c=None) -> str | None:
    return RC.key(gate, command, repo, c or cfg())


def test_the_knobs_exist():
    s = Config().resultcache
    assert s.enabled is True and s.keep_days >= 1 and s.max_mb >= 1


def test_the_same_tree_command_and_environment_give_one_key(tmp_path: Path):
    repo = project(tmp_path)
    assert key(repo) == key(repo) and key(repo) is not None


def test_a_change_to_one_code_file_misses(tmp_path: Path):
    repo = project(tmp_path)
    before = key(repo)
    commit(repo, "a.py", "x = 2\n")
    assert key(repo) != before


def test_a_different_command_misses(tmp_path: Path):
    repo = project(tmp_path)
    assert key(repo, "pytest tests/ -q") != key(repo, "pytest tests/ -q -n 4 --other")


def test_a_different_gate_misses(tmp_path: Path):
    repo = project(tmp_path)
    assert key(repo, gate="ci") != key(repo, gate="unit_tests")


def test_a_lockfile_or_tool_version_change_misses(tmp_path: Path, monkeypatch):
    repo = project(tmp_path)
    before = key(repo)
    monkeypatch.setattr(RC, "_python", lambda: "3.99.0")
    assert key(repo) != before
    monkeypatch.undo()
    commit(repo, "uv.lock", "version = 2\n")
    assert key(repo) != before


def test_an_events_only_or_docs_only_commit_hits(tmp_path: Path):
    repo = project(tmp_path)
    before = key(repo)
    commit(repo, ".ddflow/events/e2.jsonl", "{}\n")
    commit(repo, "docs/more.md", "more\n")
    assert key(repo) == before


def test_a_dirty_tree_is_keyed_by_its_content_not_left_unplaced(tmp_path: Path):
    repo = project(tmp_path)
    clean = key(repo)
    (repo / "a.py").write_text("x = 3\n")
    dirty = key(repo)
    assert dirty is not None and dirty != clean
    (repo / "a.py").write_text("x = 4\n")
    assert key(repo) not in (None, dirty)  # other content, other key
    (repo / "a.py").write_text("x = 3\n")
    assert key(repo) == dirty  # same content again: same key
    (repo / "new.py").write_text("y = 1\n")  # an untracked file is code too
    assert key(repo) != dirty


def test_no_readable_tree_means_no_key(tmp_path: Path):
    assert key(tmp_path / "nowhere") is None
    plain = tmp_path / "plain"
    plain.mkdir()
    assert key(plain) is None


def test_two_runs_on_one_tree_run_the_command_once(tmp_path: Path):
    repo = project(tmp_path)
    c = cfg()
    k = key(repo, c=c)
    assert RC.lookup(repo, k, c) is None
    RC.store(repo, k, "passed", {"command": "x", "exit": 0, "elapsed_s": 5.0}, c)
    hit = RC.lookup(repo, k, c)
    assert hit is not None and hit["outcome"] == "passed"


def test_a_failed_result_is_served_only_when_asked_and_then_as_failed(tmp_path: Path):
    repo = project(tmp_path)
    c = cfg()
    k = key(repo, c=c)
    RC.store(repo, k, "failed", {"exit": 1}, c)
    assert RC.lookup(repo, k, c) is None  # a rerun of a failed gate really runs (flake accounting)
    assert RC.entry_path(repo, k).exists()  # but the failure is kept
    c = cfg(reuse_failed=True)
    k = key(repo, c=c)
    RC.store(repo, k, "failed", {"exit": 1, "failed_tests": ["t::a"]}, c)
    assert RC.lookup(repo, k, c)["outcome"] == "failed"


def test_unavailable_partial_and_unknown_outcomes_are_never_stored(tmp_path: Path):
    repo = project(tmp_path)
    c = cfg()
    k = key(repo, c=c)
    for bad in ("unavailable", "partial", "skipped", "", "weird"):
        RC.store(repo, k, bad, {"exit": 0}, c)
        assert RC.lookup(repo, k, c) is None, bad


def test_a_corrupt_or_tampered_entry_is_a_miss(tmp_path: Path):
    repo = project(tmp_path)
    c = cfg()
    k = key(repo, c=c)
    RC.store(repo, k, "passed", {"exit": 0}, c)
    path = RC.entry_path(repo, k)
    path.write_text("{not json")
    assert RC.lookup(repo, k, c) is None
    RC.store(repo, k, "passed", {"exit": 0}, c)
    data = json.loads(path.read_text())
    data["key"] = "someone-elses"
    path.write_text(json.dumps(data))
    assert RC.lookup(repo, k, c) is None
    path.write_text(
        json.dumps({"key": k, "outcome": "unavailable", "created": time.time(), "evidence": {}})
    )
    assert RC.lookup(repo, k, c) is None  # an entry that says unavailable is never a hit


def test_a_second_project_never_reads_the_first_projects_entries(tmp_path: Path):
    one, two = project(tmp_path, "one"), project(tmp_path, "two")
    c = cfg()
    k = key(one, c=c)
    RC.store(one, k, "passed", {"exit": 0}, c)
    assert RC.lookup(two, k, c) is None  # same key, other project
    assert RC.entry_path(one, k).parent != RC.entry_path(two, k).parent
    assert ".ddflow/local" in str(RC.entry_path(one, k))


def test_a_worktree_shares_the_projects_cache(tmp_path: Path):
    repo = project(tmp_path)
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "side", str(wt))
    assert RC.entry_path(repo, "k").parent == RC.entry_path(wt, "k").parent
    c = cfg()
    k_main, k_wt = key(repo, c=c), key(wt, c=c)
    assert k_main == k_wt  # the same tree under another path is the same key
    RC.store(repo, k_main, "passed", {"exit": 0}, c)
    assert RC.lookup(wt, k_wt, c) is not None


def test_the_off_switch_stores_and_hits_nothing(tmp_path: Path):
    repo = project(tmp_path)
    off = cfg(enabled=False)
    assert key(repo, c=off) is None
    RC.store(repo, "k", "passed", {}, off)
    assert not RC.entry_path(repo, "k").exists()


def test_old_entries_are_pruned_by_age_then_the_oldest_by_size(tmp_path: Path):
    repo = project(tmp_path)
    c = cfg(keep_days=1, max_mb=1)
    d = RC.entry_path(repo, "x").parent
    d.mkdir(parents=True, exist_ok=True)
    old = d / ("a" * 64 + ".json")
    old.write_text(
        json.dumps({"key": "a" * 64, "outcome": "passed", "created": 1.0, "evidence": {}})
    )
    os.utime(old, (1.0, 1.0))
    big = [d / (f"{i:064d}.json") for i in range(4)]
    for i, p in enumerate(big):
        p.write_text("x" * 400_000)
        os.utime(p, (time.time() - 100 + i, time.time() - 100 + i))
    RC.prune(repo, c)
    assert not old.exists()  # past keep_days
    left = [p for p in big if p.exists()]
    assert sum(p.stat().st_size for p in left) <= 1_000_000
    assert big[-1] in left and big[0] not in left  # oldest pruned first


def test_a_hit_is_recorded_with_its_source_and_never_claims_the_old_log(tmp_path: Path):
    repo = project(tmp_path)
    c = cfg()
    k = key(repo, c=c)
    RC.store(
        repo,
        k,
        "passed",
        {
            "command": "x",
            "exit": 0,
            "elapsed_s": 300.0,
            "output_log": ".ddflow/runs/old.log",
            "tail": "ok",
        },
        c,
    )
    ev = RC.hit_evidence(RC.lookup(repo, k, c), k)
    assert ev["cached"] is True and "cached from run" in ev["note"]
    assert ev["elapsed_s"] == 0.0 and ev["cached_elapsed_s"] == 300.0
    assert "output_log" not in ev


def _gate(tmp_path: Path) -> GateDef:
    return GateDef(id="unit_tests", title="ut", command=f"echo ran >> '{tmp_path}/count.txt'")


def test_the_gate_runner_runs_the_command_once_for_one_tree(tmp_path: Path, monkeypatch):
    repo = project(tmp_path)
    c = Config()
    gdef = _gate(tmp_path)
    monkeypatch.setattr(GR, "_runs_whole_suite", lambda g: False)
    first = GR.run_command_gate(gdef, repo, cfg=c)
    second = GR.run_command_gate(gdef, repo, cfg=c)
    assert first[0] == second[0] == "passed"
    assert (tmp_path / "count.txt").read_text().count("ran") == 1
    assert second[1].get("cached") is True and not first[1].get("cached")
    commit(repo, "a.py", "x = 9\n")
    GR.run_command_gate(gdef, repo, cfg=c)
    assert (tmp_path / "count.txt").read_text().count("ran") == 2  # a changed tree runs again


def test_a_failed_run_is_never_reported_as_passed_by_the_cache(tmp_path: Path):
    repo = project(tmp_path)
    gdef = GateDef(id="unit_tests", title="ut", command=f"echo ran >> '{tmp_path}/c2.txt'; exit 1")
    c = Config()
    c.resultcache.reuse_failed = True
    a = GR.run_command_gate(gdef, repo, cfg=c)
    b = GR.run_command_gate(gdef, repo, cfg=c)
    assert a[0] == b[0] == "failed" and b[1].get("cached") is True
    assert (tmp_path / "c2.txt").read_text().count("ran") == 1
    c2 = Config()  # default: a failed gate reruns on the same tree
    GR.run_command_gate(gdef, repo, cfg=c2)
    assert (tmp_path / "c2.txt").read_text().count("ran") == 2


def test_a_changed_tool_version_misses(tmp_path: Path, monkeypatch):
    repo = project(tmp_path)
    c = cfg(probe_commands=["mytool --version"])
    monkeypatch.setattr(RC, "_probe", lambda cmd: "v1")
    before = key(repo, c=c)
    monkeypatch.setattr(RC, "_probe", lambda cmd: "v2")
    assert key(repo, c=c) != before


def test_an_unreadable_lock_file_means_no_key(tmp_path: Path):
    repo = project(tmp_path)
    commit(repo, "uv.lock", "v = 1\n")
    (repo / "uv.lock").chmod(0)
    try:
        if os.access(repo / "uv.lock", os.R_OK):  # running as a user that ignores modes
            return
        assert key(repo) is None
    finally:
        (repo / "uv.lock").chmod(0o644)


def test_a_file_mode_is_part_of_the_code(tmp_path: Path):
    repo = project(tmp_path)
    (repo / "run.sh").write_text("echo hi\n")
    plain = key(repo)
    (repo / "run.sh").chmod(0o755)
    assert key(repo) != plain


def test_a_submodule_at_another_commit_misses(tmp_path: Path):
    lib = project(tmp_path, "lib")
    repo = project(tmp_path, "main")
    git(repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(lib), "lib")
    git(repo, "commit", "-q", "-m", "add lib")
    before = key(repo)
    commit(lib, "b.py", "y = 1\n")
    git(repo / "lib", "-c", "protocol.file.allow=always", "pull", "-q", "origin", "main")
    assert key(repo) not in (None, before)


def test_a_shorter_gate_timeout_does_not_inherit_a_pass(tmp_path: Path):
    repo = project(tmp_path)
    c = Config()
    one = GateDef(
        id="unit_tests", title="ut", command=f"echo ran >> '{tmp_path}/t.txt'", timeout_s=600
    )
    two = GateDef(
        id="unit_tests", title="ut", command=f"echo ran >> '{tmp_path}/t.txt'", timeout_s=5
    )
    GR.run_command_gate(one, repo, cfg=c)
    GR.run_command_gate(two, repo, cfg=c)
    assert (tmp_path / "t.txt").read_text().count("ran") == 2
