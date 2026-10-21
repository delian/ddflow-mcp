"""`update --globs` on a claimed item refused for an overlap (B-shared-glob-update-waits).

The refusal said only "overlaps X held by Y", so the agent could neither tell how long the
holder had had it nor that the overlap came from a directory glob covering files the
holder never touched. It now names holder, age and every overlapping pair; allows files the
holder's own changes do not include; and a finished holder's lease holds nothing.
"""

from __future__ import annotations

import time

from conftest import run_cli
from helpers import items_of as _items

from ddflow.api import items as AI
from ddflow.api import lifecycle as A
from ddflow.config import Config
from ddflow.core.model import DONE
from ddflow.infra.log import EventLog
from ddflow.services import leases as L

HOLDER, ME = "agent-holder", "agent-me"


def _setup(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "H1", "--globs", "pkg/gates/**")
    run_cli(repo, "task", "add", "M1", "--globs", "mine.py")
    assert A.claim(repo, "H1", no_worktree=True, agent=HOLDER).ok
    assert A.claim(repo, "M1", no_worktree=True, agent=ME).ok


def test_refusal_names_holder_age_pairs_and_a_narrower_glob(repo):
    _setup(repo)
    code, out, err = run_cli(repo, "update", "M1", "--globs", "mine.py,pkg/gates/x.py", agent=ME)
    text = out + err
    assert code != 0
    assert HOLDER in text and "H1" in text
    assert "for 0 min" in text, text
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
