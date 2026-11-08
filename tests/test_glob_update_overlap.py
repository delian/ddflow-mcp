"""`update --globs` on a claimed item refused for an overlap (B-shared-glob-update-waits).

The refusal said only "overlaps X held by Y", so the agent could neither tell how long the
holder had had it nor that the overlap came from a directory glob covering files the
holder never touched. It now names holder, age and every overlapping pair; allows files the
holder's own changes do not include; and a finished holder's lease holds nothing.
"""

from __future__ import annotations

import json
import re
import time

from conftest import run_cli
from helpers import items_of as _items

from ddflow.api import items as AI
from ddflow.api import lifecycle as A
from ddflow.config import Config
from ddflow.core.model import DONE, fold
from ddflow.infra.log import EventLog
from ddflow.services import leases as L
from ddflow.services import waiting as WG

HOLDER, ME = "agent-holder", "agent-me"


def _state(repo):
    return fold(EventLog(repo, "reader").read_all(), strict=False)


def _cfg(repo):
    return Config.load(repo)


def _setup(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "H1", "--title", "H1 fixture", "--globs", "pkg/gates/**")
    run_cli(repo, "task", "add", "M1", "--title", "M1 fixture", "--globs", "mine.py")
    assert A.claim(repo, "H1", no_worktree=True, agent=HOLDER).ok
    assert A.claim(repo, "M1", no_worktree=True, agent=ME).ok


def test_partial_widening_accepts_the_non_overlapping_subset(repo):
    """B-ddflow-glob-widen-refused-broad: a widening that only PARTLY overlaps another
    lease takes the non-overlapping subset instead of refusing the whole list (and names
    what was withheld)."""
    _setup(repo)
    ok = AI.update(
        repo, "M1", AI.ItemEdit(globs=["mine.py", "docs/r.md", "pkg/gates/x.py"]), agent=ME
    )
    assert ok.ok, ok.reason
    assert ok.data["globs_withheld"] == ["pkg/gates/x.py"], ok.data
    item = _items(repo)["M1"]
    assert item.globs == ["mine.py", "docs/r.md"], item.globs
    assert item.lease.globs == ["mine.py", "docs/r.md"], item.lease.globs


def test_refusal_names_holder_age_pairs_and_a_narrower_glob(repo):
    _setup(repo)
    code, out, err = run_cli(
        repo, "update", "M1", "--globs", "pkg/gates/x.py,pkg/gates/y.py", agent=ME
    )
    text = out + err
    assert code != 0
    assert HOLDER in text and "H1" in text
    assert re.search(r"for \d+ min", text), text
    assert "'pkg/gates/x.py' overlaps 'pkg/gates/**'" in text
    assert "narrower" in text.lower()


def test_files_the_holder_does_not_change_are_allowed(repo, monkeypatch):
    _setup(repo)
    monkeypatch.setattr(L, "holder_changed_paths", lambda log, item: ["pkg/gates/other.py"])
    ok = AI.update(repo, "M1", AI.ItemEdit(globs=["mine.py", "pkg/gates/x.py"]), agent=ME)
    assert ok.ok, ok.reason
    assert "pkg/gates/x.py" in _items(repo)["M1"].lease.globs
    # but a file the holder DID change stays refused
    bad = AI.update(repo, "M1", AI.ItemEdit(globs=["mine.py", "pkg/gates/other.py"]), agent=ME)
    assert not bad.ok


def test_a_finished_holders_lease_blocks_nothing(repo):
    _setup(repo)
    log = EventLog(repo, "probe")
    st = L._fold(log)
    cfg = Config.load(repo)
    me = st.items["M1"]
    now = time.time()
    assert L.glob_clash(st, cfg, me, ME, ["pkg/gates/x.py"], now) is not None
    st.items["H1"].state = DONE
    assert L.glob_clash(st, cfg, me, ME, ["pkg/gates/x.py"], now) is None


def test_keep_list_counts_every_holder(repo):
    _setup(repo)
    run_cli(repo, "task", "add", "H2", "--title", "H2 fixture", "--globs", "other/**")
    assert A.claim(repo, "H2", no_worktree=True, agent="agent-h2").ok
    ok = AI.update(
        repo, "M1", AI.ItemEdit(globs=["pkg/gates/a.py", "other/b.py", "fine.py"]), agent=ME
    )
    # a REPLACEMENT (not a widening) with overlaps is still refused: only a widening that
    # keeps every held glob is accepted partially (B-ddflow-glob-widen-refused-broad).
    assert not ok.ok
    assert "other/b.py' overlaps" in ok.reason and "pkg/gates/a.py' overlaps" in ok.reason
    assert "(fine.py would be accepted)" in ok.reason


def test_all_globs_clashing_does_not_promise_an_empty_acceptance(repo):
    _setup(repo)
    bad = AI.update(repo, "M1", AI.ItemEdit(globs=["pkg/gates/a.py"]), agent=ME)
    assert not bad.ok and "would be accepted" not in bad.reason


def test_bracket_filenames_are_matched_literally(repo, monkeypatch):
    _setup(repo)
    monkeypatch.setattr(L, "holder_changed_paths", lambda log, item: ["pkg/gates/f[1].py"])
    ok = AI.update(repo, "M1", AI.ItemEdit(globs=["mine.py", "pkg/gates/f1.py"]), agent=ME)
    assert ok.ok, ok.reason


def test_holder_on_another_release_line_is_no_clash(repo):
    _setup(repo)
    log = EventLog(repo, "probe")
    st = L._fold(log)
    cfg = Config.load(repo)
    st.items["H1"].line = "v9"
    clash = L._clash_text(log, cfg, st, st.items["M1"], ["pkg/gates/x.py"], time.time())
    assert clash is None


def test_a_shared_file_the_holder_changed_is_inert(repo, monkeypatch):
    _setup(repo)
    monkeypatch.setattr(L, "holder_changed_paths", lambda log, item: ["pkg/gates/CHANGELOG.md"])
    monkeypatch.setattr(L, "shared_globs", lambda cfg: ["pkg/gates/CHANGELOG.md"])
    ok = AI.update(repo, "M1", AI.ItemEdit(globs=["mine.py", "pkg/gates/**"]), agent=ME)
    assert ok.ok, ok.reason


def test_rereating_a_shared_glob_is_not_queued_behind_a_waiter(repo):
    """B-update-queue-ignores-shared-globs: re-stating a shared glob the item already holds
    must not be refused as 'queued behind' an earlier waiter that also asked for it."""
    run_cli(repo, "config", "lease.shared_globs", '["README.md"]')
    run_cli(repo, "task", "add", "H1", "--title", "holder", "--globs", "changes.toml")
    run_cli(repo, "task", "add", "Q1", "--title", "queuer", "--globs", "changes.toml,README.md")
    run_cli(repo, "task", "add", "M1", "--title", "mine", "--globs", "README.md")
    assert A.claim(repo, "H1", no_worktree=True, agent=HOLDER).ok
    assert A.claim(repo, "M1", no_worktree=True, agent=ME).ok
    # Q1's claim is queued: changes.toml is held by HOLDER; README.md is shared.
    assert not A.claim(repo, "Q1", no_worktree=True, agent="agent-queuer", queue=True).ok
    ok = AI.update(repo, "M1", AI.ItemEdit(globs=["README.md", "other.py"]), agent=ME)
    assert ok.ok, ok.reason
    assert _items(repo)["M1"].globs == ["README.md", "other.py"], _items(repo)["M1"].globs


def test_re_stating_a_held_glob_is_not_queued_behind_a_waiter(repo):
    """B-update-globs-queued-refusal-vague: re-stating a NON-shared glob the item already
    holds must not be refused as 'queued behind' an earlier waiter that also asked for it.
    The refusal must name which file, not 'those files', and `--json` must stay JSON."""
    run_cli(repo, "task", "add", "H1", "--title", "holder", "--globs", "changes.toml")
    run_cli(repo, "task", "add", "Q1", "--title", "queuer", "--globs", "changes.toml,README.md")
    run_cli(repo, "task", "add", "M1", "--title", "mine", "--globs", "README.md")
    assert A.claim(repo, "H1", no_worktree=True, agent=HOLDER).ok
    assert A.claim(repo, "M1", no_worktree=True, agent=ME).ok
    # Q1's claim is queued: changes.toml is held by HOLDER.
    assert not A.claim(repo, "Q1", no_worktree=True, agent="agent-queuer", queue=True).ok
    # M1 ALREADY HOLDS README.md: re-stating it is not a new demand, the widening takes
    # the new glob.
    ok = AI.update(repo, "M1", AI.ItemEdit(globs=["README.md", "other.py"]), agent=ME)
    assert ok.ok, ok.reason
    assert _items(repo)["M1"].globs == ["README.md", "other.py"], _items(repo)["M1"].globs


def test_a_queued_refusal_names_the_file_and_stays_json(repo):
    """The queued-behind refusal must name the file it refuses and keep `--json` parseable
    (B-update-globs-queued-refusal-vague)."""
    run_cli(repo, "task", "add", "H1", "--title", "holder", "--globs", "changes.toml")
    run_cli(repo, "task", "add", "Q1", "--title", "queuer", "--globs", "changes.toml")
    run_cli(repo, "task", "add", "M1", "--title", "mine", "--globs", "other.py")
    assert A.claim(repo, "H1", no_worktree=True, agent=HOLDER).ok
    assert A.claim(repo, "M1", no_worktree=True, agent=ME).ok
    assert not A.claim(repo, "Q1", no_worktree=True, agent="agent-queuer", queue=True).ok
    # M1 asks for changes.toml, which Q1 already queued for: refused, named, JSON.
    code, out, err = run_cli(
        repo, "update", "M1", "--globs", "other.py,changes.toml", "--json", agent=ME
    )
    assert code != 0
    json.loads(out)
    assert "changes.toml" in (out + err), (out, err)


def test_widening_keeps_a_held_glob_a_waiter_queued_for(repo):
    """B-widen-refused-on-own-held-glob: red-b holds tests/test_no_agent_literals.py, red-h
    queued for that exact glob, and red-b widens by adding an unrelated free file while
    re-stating the held glob. The re-statement is not a new demand, so the widening is not
    refused 'queued behind red-h', and no false wait cycle is recorded."""
    run_cli(repo, "init")
    run_cli(
        repo,
        "task",
        "add",
        "RB",
        "--title",
        "red-b",
        "--globs",
        "tests/test_no_agent_literals.py",
    )
    run_cli(
        repo,
        "task",
        "add",
        "RH",
        "--title",
        "red-h",
        "--globs",
        "tests/test_no_agent_literals.py",
    )
    assert A.claim(repo, "RB", no_worktree=True, agent="red-b").ok
    # red-h queues for the one glob red-b holds (lease.waiting).
    assert not A.claim(repo, "RH", no_worktree=True, agent="red-h", queue=True).ok
    # Widening: keep the held glob, add an unrelated free file. Must not be refused.
    ok = AI.update(
        repo,
        "RB",
        AI.ItemEdit(
            globs=["tests/test_no_agent_literals.py", "docs/ddflow/research/driver-inventory.md"]
        ),
        agent="red-b",
    )
    assert ok.ok, ok.reason
    assert _items(repo)["RB"].globs == [
        "tests/test_no_agent_literals.py",
        "docs/ddflow/research/driver-inventory.md",
    ], _items(repo)["RB"].globs
    # No false wait cycle: red-b is not recorded waiting on red-h. red-h legitimately
    # still waits on red-b for the held glob, so the cycle would only exist if red-b asked
    # for something red-h holds -- it does not.
    graph = WG.wait_for_graph(_state(repo), _cfg(repo), time.time())
    assert "RB" not in graph, graph
    assert graph.get("RH") == ["RB"], graph


def test_a_widening_refused_for_a_new_glob_names_only_that_glob(repo):
    """B-widen-refused-on-own-held-glob: a widening that re-states a HELD glob and asks for
    one NEW glob an earlier waiter queued for first is refused naming that one contended
    glob -- not 'those globs', and not the held glob the requester already owns."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "H1", "--title", "holder", "--globs", "changes.toml")
    run_cli(repo, "task", "add", "Q1", "--title", "queuer", "--globs", "changes.toml")
    run_cli(
        repo, "task", "add", "M1", "--title", "mine", "--globs", "tests/test_no_agent_literals.py"
    )
    assert A.claim(repo, "H1", no_worktree=True, agent="holder").ok
    assert A.claim(repo, "M1", no_worktree=True, agent="mine").ok
    # Q1 queues for changes.toml, which H1 holds.
    assert not A.claim(repo, "Q1", no_worktree=True, agent="queuer", queue=True).ok
    # M1 re-states its held glob and asks for changes.toml. Only changes.toml contends.
    code, out, err = run_cli(
        repo,
        "update",
        "M1",
        "--globs",
        "tests/test_no_agent_literals.py,changes.toml",
        agent="mine",
    )
    assert code != 0
    assert "changes.toml" in (out + err), (out, err)
    # The vague wording is gone: the refusal names the one contended glob.
    assert "those globs" not in (out + err), (out, err)


def test_an_exact_file_the_holder_claimed_is_never_free(repo, monkeypatch):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "H1", "--title", "H1 fixture", "--globs", "pkg/exact.py")
    run_cli(repo, "task", "add", "M1", "--title", "M1 fixture", "--globs", "mine.py")
    assert A.claim(repo, "H1", no_worktree=True, agent=HOLDER).ok
    assert A.claim(repo, "M1", no_worktree=True, agent=ME).ok
    monkeypatch.setattr(L, "holder_changed_paths", lambda log, item: [])
    bad = AI.update(repo, "M1", AI.ItemEdit(globs=["mine.py", "pkg/exact.py"]), agent=ME)
    assert not bad.ok


def test_holder_changed_paths_reads_the_real_worktree(repo):
    import subprocess

    _setup(repo)
    log = EventLog(repo, "probe")
    item = L._fold(log).items["H1"]
    wt = repo / ".ddflow" / "wt-h1"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-q", "-b", "h1-br", str(wt)], check=True
    )
    (wt / "pkg" / "gates").mkdir(parents=True)
    (wt / "pkg" / "gates" / "other.py").write_text("x = 1\n")
    item.worktree = str(wt)
    item.branch = "h1-br"
    assert L.holder_changed_paths(log, item) == ["pkg/gates/other.py"]
