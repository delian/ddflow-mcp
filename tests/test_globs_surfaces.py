"""Globs on every surface say what was given and what is held.

- Bdc85898c40 / Bb3cb64444e: `--globs` repeated kept only the LAST value (a plain
  argparse store), and the claim never said what it recorded;
- the api claim split a JSON array on its commas (MCP sends one string);
- Bd8038b08a1: `update --globs` replaces the list -- by design, as the implement gate
  says -- but said nothing about what it dropped, and `show` never printed a lease's globs;
- B7036cf788c: `wait --item` judged the item's stored globs, not the globs the next
  claim would take, so READY was followed by a refused claim;
- B7f8060f2f5: a heartbeat reviving a lapsed lease caught up its globs but not its
  resources, and `claim --resources` would have been undone by such a catch-up.
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
from ddflow.core import outcome as O
from ddflow.core.model import fold
from ddflow.infra.log import EventLog

HOLDER, OTHER = "agent-holder", "agent-other"


def _items(repo: Path):
    return fold(EventLog(repo, "probe").read_all(), strict=False).items


def _lease_globs(repo: Path, item: str) -> list[str]:
    lz = _items(repo)[item].lease
    assert lz is not None, f"{item} has no lease"
    return sorted(lz.globs)


def test_a_repeated_globs_flag_claims_every_value_and_says_so(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1")
    flags = [x for p in ("src/a.py", "src/b.py,src/c.py", "docs/d.md") for x in ("--globs", p)]
    code, out, err = run_cli(repo, "claim", "T1", "--no-worktree", *flags, agent=HOLDER)
    assert code == O.OK, err
    want = ["docs/d.md", "src/a.py", "src/b.py", "src/c.py"]
    assert _lease_globs(repo, "T1") == want
    globs_line = next(ln for ln in out.splitlines() if "globs:" in ln)
    assert all(g in globs_line for g in want), out


def test_a_repeated_globs_flag_on_add_update_and_split_keeps_every_value(repo):
    run_cli(repo, "init")
    code, _o, err = run_cli(repo, "task", "add", "T1", "--globs", "a.py", "--globs", "b.py")
    assert code == O.OK, err
    assert sorted(_items(repo)["T1"].globs) == ["a.py", "b.py"]
    code, _o, err = run_cli(repo, "phase", "add", "P1", "--globs", "p.py", "--globs", "q.py")
    assert code == O.OK, err
    assert sorted(_items(repo)["P1"].globs) == ["p.py", "q.py"]
    code, _o, err = run_cli(repo, "update", "T1", "--globs", "c.py", "--globs", "d.py")
    assert code == O.OK, err
    assert sorted(_items(repo)["T1"].globs) == ["c.py", "d.py"]
    code, _o, err = run_cli(
        repo, "split", "T1", "--into", "T1a", "--into", "T1b", "--globs", "e.py", "--globs", "f.py"
    )
    assert code == O.OK, err
    assert sorted(_items(repo)["T1a"].globs) == ["e.py", "f.py"]


def test_a_json_array_is_read_whole_by_claim_and_update(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1")
    raw = json.dumps(["src/speaker.py", "config/speaker.py"])
    out = A.claim(repo, "T1", globs=raw, no_worktree=True, agent=HOLDER)  # as MCP sends it
    assert out.ok, out.reason
    assert _lease_globs(repo, "T1") == ["config/speaker.py", "src/speaker.py"]
    code, _o, err = run_cli(repo, "update", "T1", "--globs", json.dumps(["x.py", "y.py"]))
    assert code == O.OK, err
    assert sorted(_items(repo)["T1"].globs) == ["x.py", "y.py"]


def test_update_globs_names_what_it_dropped(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py,b.py")
    assert A.claim(repo, "T1", no_worktree=True, agent=HOLDER).ok
    code, out, err = run_cli(repo, "update", "T1", "--globs", "b.py,c.py", agent=HOLDER)
    assert code == O.OK, err
    text = out + err
    assert "dropped" in text and "a.py" in text, text
    assert _lease_globs(repo, "T1") == ["b.py", "c.py"]
    out_ = AI.update(repo, "T1", AI.ItemEdit(globs=["c.py"]), agent=HOLDER)
    assert out_.data["globs_dropped"] == ["b.py"]


def test_show_prints_the_lease_globs(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    assert A.claim(repo, "T1", globs="a.py,z.py", no_worktree=True, agent=HOLDER).ok
    code, out, err = run_cli(repo, "show", "T1")
    assert code == O.OK, err
    lease_line = next(ln for ln in out.splitlines() if ln.strip().startswith("lease"))
    assert "a.py" in lease_line and "z.py" in lease_line, out


def test_wait_judges_the_globs_the_claim_will_take(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "docs/RESEARCH.md")
    run_cli(repo, "task", "add", "T2", "--globs", "src/b.py")
    assert A.claim(repo, "T1", no_worktree=True, agent=OTHER).ok
    out = A.wait(repo, item="T2", globs="src/b.py,docs/RESEARCH.md", timeout_s=0, agent=HOLDER)
    assert out.exit != O.OK, "READY, yet the claim with these globs is refused"
    assert "docs/RESEARCH.md" in out.reason
    assert (
        A.claim(repo, "T2", globs="src/b.py,docs/RESEARCH.md", no_worktree=True, agent=HOLDER).exit
        == O.REFUSED
    )  # ...and that is what the claim says too
    code, _o, err = run_cli(
        repo, "wait", "--item", "T2", "--globs", "src/b.py", "--timeout", "0", agent=HOLDER
    )
    assert code == O.OK, err


def test_a_revived_lease_catches_up_resources_and_keeps_a_claimed_override(repo, monkeypatch):
    run_cli(repo, "init")
    code, _o, err = run_cli(repo, "config", "--set", "schedule.resources", '["gpu=4"]')
    assert code == O.OK, err
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(re.sub(r"(?m)^\[lease\]$", "[lease]\ngrace_s = 0", cfg.read_text(), count=1))
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "ddflow"], check=True)
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    monkeypatch.setenv("DDFLOW_LEASE_TTL_S", "1")
    code, out, err = run_cli(
        repo, "claim", "T1", "--no-worktree", "--resources", "gpu:1", agent=HOLDER
    )
    assert code == O.OK, out + err
    monkeypatch.delenv("DDFLOW_LEASE_TTL_S")
    assert _items(repo)["T1"].resources == ["gpu:1"], "the claim's override is the item's"
    time.sleep(2.5)  # lapsed; an edit now reaches only the item
    assert AI.update(repo, "T1", AI.ItemEdit(resources=["gpu:3"]), agent=HOLDER).ok
    assert run_cli(repo, "heartbeat", "T1", agent=HOLDER)[0] == O.OK
    assert _items(repo)["T1"].lease.resources == ["gpu:3"]


def test_the_mcp_wait_tool_takes_the_claims_globs(repo):
    from ddflow.surfaces.mcp import TOOLS

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "docs/RESEARCH.md")
    run_cli(repo, "task", "add", "T2", "--globs", "src/b.py")
    assert A.claim(repo, "T1", no_worktree=True, agent=OTHER).ok
    args = {"item": "T2", "globs": '["src/b.py", "docs/RESEARCH.md"]', "timeout": 0}
    out = TOOLS["ddflow_wait"]["api"](repo, args, HOLDER)
    assert out.exit != O.OK and "docs/RESEARCH.md" in out.reason
