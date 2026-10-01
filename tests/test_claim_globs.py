"""A claim keeps the globs it was given (Bbd07ab69fd, Bc5aec031b3).

`claim --globs` set the LEASE's globs only. The heartbeat's catch-up (Bb21d338f26) points
a renewed lease at the ITEM's stored globs whenever the two differ -- so the first
heartbeat after a claim put the lease back on whatever the item had declared before:

- home-simulator 34.10g: 11 claimed paths replaced by the item's original 4, 10 s after
  the claim, taking a file another agent held and dropping 7 being written;
- run_nemo_run 159.A.9: a heartbeat reviving a lapsed lease copied the item's EMPTY
  globs over the 5 claimed, and the commit hook then refused every staged path.

The claim now records its globs on the item too, so there is one answer to "what does
this claim cover" and nothing for a catch-up to undo. A glob a JSON array or shell
quoting left mangled is refused rather than recorded as a path that matches nothing.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import run_cli

from ddflow.api import items as AI
from ddflow.api import lifecycle as A
from ddflow.core import globspec as GS
from ddflow.core import outcome as O
from ddflow.core.model import fold
from ddflow.infra.log import EventLog

HOLDER = "agent-holder"


def _items(repo: Path):
    return fold(EventLog(repo, "probe").read_all(), strict=False).items


def _lease_globs(repo: Path, item: str) -> list[str]:
    lz = _items(repo)[item].lease
    assert lz is not None, f"{item} has no lease"
    return sorted(lz.globs)


def test_a_heartbeat_keeps_the_globs_the_claim_took(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "src/stored.py,src/shared.py")
    out = A.claim(repo, "T1", globs="src/stored.py,src/new.py", no_worktree=True, agent=HOLDER)
    assert out.ok, out.reason
    assert A.heartbeat(repo, "T1", agent=HOLDER).ok
    assert _lease_globs(repo, "T1") == ["src/new.py", "src/stored.py"]
    # One source of truth: the item says what the lease holds.
    assert sorted(_items(repo)["T1"].globs) == ["src/new.py", "src/stored.py"]


def test_a_claim_without_globs_leaves_the_item_alone(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    before = len(EventLog(repo, "probe").read_all())
    assert A.claim(repo, "T1", no_worktree=True, agent=HOLDER).ok
    kinds = [e.kind for e in EventLog(repo, "probe").read_all()[before:]]
    assert "task.updated" not in kinds, kinds
    assert _lease_globs(repo, "T1") == ["a.py"]


def _lapsing_claim(repo: Path, monkeypatch) -> None:
    """T1 has NO stored globs; HOLDER claims it with two, and the lease lapses."""
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(re.sub(r"(?m)^\[lease\]$", "[lease]\ngrace_s = 0", cfg.read_text(), count=1))
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "ddflow"], check=True)
    run_cli(repo, "task", "add", "T1", "--title", "t")
    monkeypatch.setenv("DDFLOW_LEASE_TTL_S", "1")
    code, out, err = run_cli(
        repo, "claim", "T1", "--no-worktree", "--globs", "src/*,docs/x.md", agent=HOLDER
    )
    assert code == O.OK, out + err
    monkeypatch.delenv("DDFLOW_LEASE_TTL_S")
    time.sleep(2.5)  # past its TTL; grace is 0


def test_reviving_a_lapsed_lease_keeps_the_claimed_globs(repo, monkeypatch):
    _lapsing_claim(repo, monkeypatch)
    code, out, err = run_cli(repo, "heartbeat", "T1", agent=HOLDER)
    assert code == O.OK, out + err
    assert _lease_globs(repo, "T1") == ["docs/x.md", "src/*"], "revival wiped the claim"


def test_after_a_revival_the_hook_accepts_a_path_the_claim_covers(repo, monkeypatch):
    _lapsing_claim(repo, monkeypatch)
    assert run_cli(repo, "heartbeat", "T1", agent=HOLDER)[0] == O.OK
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "src/a.py"], check=True)
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        "DDFLOW_AGENT": HOLDER,
    }
    p = subprocess.run(
        [sys.executable, "-m", "ddflow", "--repo", str(repo), "hooks", "check-commit"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    assert "not covered" not in p.stdout + p.stderr, p.stdout + p.stderr


def test_a_json_array_is_read_whole_by_add_and_update(repo):
    run_cli(repo, "init")
    raw = json.dumps(["src/speaker.py", "config/speaker.py"])
    code, _o, err = run_cli(repo, "task", "add", "T1", "--globs", raw)
    assert code == O.OK, err
    assert sorted(_items(repo)["T1"].globs) == ["config/speaker.py", "src/speaker.py"]
    out = AI.update(repo, "T1", AI.ItemEdit(globs=[json.dumps(["a.py", "b.py"])]))
    assert out.ok, out.reason
    assert sorted(_items(repo)["T1"].globs) == ["a.py", "b.py"]


def test_a_mangled_glob_is_refused_by_claim_and_records_nothing(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1")
    before = len(EventLog(repo, "probe").read_all())
    out = A.claim(repo, "T1", globs='["src/a.py", "src/b.py"', no_worktree=True, agent=HOLDER)
    assert out.exit == O.REFUSED, out.reason
    assert "src/a.py" in out.reason and "JSON" in out.reason
    assert len(EventLog(repo, "probe").read_all()) == before, "a refusal recorded something"


def test_a_mangled_glob_is_refused_by_add_and_update(repo):
    run_cli(repo, "init")
    code, _o, err = run_cli(repo, "task", "add", "T1", "--globs", "'src/a.py")
    assert code != O.OK and "src/a.py" in err, err
    run_cli(repo, "task", "add", "T2", "--globs", "a.py")
    out = AI.update(repo, "T2", AI.ItemEdit(globs=['"b.py"']))
    assert out.exit != O.OK and "b.py" in out.reason
    assert _items(repo)["T2"].globs == ["a.py"]


def test_globspec_reads_what_agents_send():
    assert GS.parse("a.py, b.py") == ["a.py", "b.py"]
    assert GS.parse(["a.py", "b.py,c.py", "a.py"]) == ["a.py", "b.py", "c.py"]
    assert GS.parse('["a.py", "b/**"]') == ["a.py", "b/**"]
    assert GS.parse(None) == [] and GS.parse("") == []
    # A character class is a glob, not a mangled JSON array.
    assert GS.problem(["src/[ab].py", "x/[[]y"]) == ""
    for bad in ('["src/a.py"', '"src/b.py"]', "'c.py", "d.py'", "src/[ab.py"):
        assert GS.problem([bad]), bad


def test_claimed_globs_in_another_order_are_the_same_claim(repo):
    """Item and lease are compared as SETS -- here and in the heartbeat's catch-up -- so
    a reordered claim records no edit, and no heartbeat rewrites the lease."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py,b.py")
    before = len(EventLog(repo, "probe").read_all())
    assert A.claim(repo, "T1", globs="b.py,a.py", no_worktree=True, agent=HOLDER).ok
    assert A.heartbeat(repo, "T1", agent=HOLDER).ok
    new = EventLog(repo, "probe").read_all()[before:]
    assert [e.kind for e in new if e.kind.endswith(".updated")] == []
    assert [e for e in new if e.kind == "lease.renewed" and "globs" in e.data] == []
    assert _lease_globs(repo, "T1") == ["a.py", "b.py"]
