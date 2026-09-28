"""`ddflow update <id> --globs` on a CLAIMED item widens the lease it is held under.

Bugs B3eda99e0fe and B5d98a4da0a (one defect, hit twice on 2026-09-28): the update
widened `item.globs` and left `lease.globs` alone. The commit hook and every lease
conflict check read `lease.globs`, so the remedy the hook itself prints --
`ddflow update <ID> --globs '...'  # widen an existing claim` -- did nothing, and the
next commit warned about the same paths again.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import enforce as E

HOLDER = "agent-a"


def _claimed(repo) -> None:
    assert run_cli(repo, "init", agent=HOLDER)[0] == 0
    code, out, err = run_cli(
        repo, "task", "add", "T1", "--title", "t", "--globs", "src/a.py", agent=HOLDER
    )
    assert code == 0, out + err
    code, out, err = run_cli(repo, "claim", "T1", "--no-worktree", agent=HOLDER)
    assert code == 0, out + err


def _lease(repo):
    return fold(EventLog(repo, "reader").read_all(), strict=False).items["T1"].lease


def _lease_globs(repo) -> list[str] | None:
    lz = _lease(repo)
    return None if lz is None else list(lz.globs)


def test_update_globs_widens_the_held_lease(repo):
    _claimed(repo)
    assert _lease_globs(repo) == ["src/a.py"]
    assert run_cli(repo, "update", "T1", "--globs", "src/a.py,README.md", agent=HOLDER)[0] == 0
    assert _lease_globs(repo) == ["src/a.py", "README.md"]


def test_show_reports_the_widened_lease(repo):
    _claimed(repo)
    run_cli(repo, "update", "T1", "--globs", "src/a.py,docs/**", agent=HOLDER)
    code, out, _err = run_cli(repo, "--json", "show", "T1", agent=HOLDER)
    assert code == 0
    data = json.loads(out)
    item = data.get("item", data)
    assert item["lease"]["globs"] == ["src/a.py", "docs/**"], item["lease"]


def test_a_narrowed_glob_list_narrows_the_lease(repo):
    _claimed(repo)
    run_cli(repo, "update", "T1", "--globs", "src/a.py,docs/**", agent=HOLDER)
    run_cli(repo, "update", "T1", "--globs", "docs/**", agent=HOLDER)
    assert _lease_globs(repo) == ["docs/**"]


def test_an_update_by_someone_else_still_retargets_the_holders_lease(repo):
    """An operator widening a stuck agent's claim is the normal case; the lease stays the
    holder's, and it is not renewed on the holder's behalf."""
    _claimed(repo)
    before = _lease(repo)
    run_cli(repo, "update", "T1", "--globs", "src/a.py,docs/**", agent="operator")
    after = _lease(repo)
    assert after.holder == HOLDER
    assert after.globs == ["src/a.py", "docs/**"]
    assert after.renewed_at == before.renewed_at


def test_the_update_does_not_extend_the_lease(repo):
    _claimed(repo)
    before = _lease(repo)
    run_cli(repo, "update", "T1", "--globs", "src/a.py,docs/**", agent=HOLDER)
    after = _lease(repo)
    assert (after.renewed_at, after.ttl_s, after.expired_at) == (
        before.renewed_at,
        before.ttl_s,
        before.expired_at,
    )


def test_a_released_lease_is_not_brought_back_by_an_update(repo):
    _claimed(repo)
    assert run_cli(repo, "release", "T1", agent=HOLDER)[0] == 0
    before = _lease_globs(repo)
    run_cli(repo, "update", "T1", "--globs", "src/b.py", agent=HOLDER)
    assert _lease_globs(repo) == before
    st = fold(EventLog(repo, "reader").read_all(), strict=False)
    assert st.items["T1"].globs == ["src/b.py"]


def test_an_expired_lease_is_not_touched_by_an_update(repo):
    _claimed(repo)
    log = EventLog(repo, HOLDER)
    log.append("lease.expired", "T1", {"holder": HOLDER})
    before = _lease(repo)
    assert before is not None and before.expired_at
    n = len(log.read_all())
    run_cli(repo, "update", "T1", "--globs", "src/b.py", agent=HOLDER)
    after = _lease(repo)
    assert after.globs == ["src/a.py"] and after.ttl_s == 0
    kinds = [e.kind for e in EventLog(repo, "reader").read_all()[n:]]
    assert "lease.renewed" not in kinds, kinds


def test_update_without_globs_leaves_the_lease_alone(repo):
    _claimed(repo)
    run_cli(repo, "update", "T1", "--title", "renamed", agent=HOLDER)
    assert _lease_globs(repo) == ["src/a.py"]


def test_the_commit_hook_accepts_a_path_the_widened_claim_covers(repo):
    """The remedy the hook prints must be one that works."""
    _claimed(repo)
    (repo / "docs").mkdir()
    (repo / "docs" / "x.md").write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", "docs/x.md"], check=True)
    code, msg = E.check_commit(repo, agent=HOLDER)
    assert "docs/x.md" in msg, (code, msg)
    assert run_cli(repo, "update", "T1", "--globs", "src/a.py,docs/**", agent=HOLDER)[0] == 0
    code, msg = E.check_commit(repo, agent=HOLDER)
    assert (code, msg) == (0, ""), msg
