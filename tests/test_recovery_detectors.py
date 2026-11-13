"""`DX-recovery-detectors`: `ready_to_land`, `lapsed_alive`, `merge_interrupted`,
reboot boot-id and the MCP boot record (orphan-recovery.md research rows b, d, e, f, h).

The pure half lives in `core/health.py` — new finding kinds judged from plain facts with no
state, no clock and no I/O — while `services/healthwatch.py` gathers the facts, the
worktree layer reads and remembers the boot id, and the MCP surface records the current
boot id at server start so the next pass knows anything older than boot is dead.

The class split is the whole safety contract: a lease that lapsed while its holder's
harness is still alive is `lapsed_alive` and is NEVER recorded crashed; a finished item
that never merged is `ready_to_land` and is only PROPOSEd; a merge left mid-flight or
landed-but-unlogged is `merge_interrupted`. Only SAFE_AUTO actions may ever be applied;
PROPOSE and OPERATOR are findings shown, never done.
"""

from __future__ import annotations

from pathlib import Path

from ddflow.core import health as H
from ddflow.infra import worktree as W


def _limits() -> H.Limits:
    return H.Limits()


class TestLapsedAlive:
    def test_lapsed_but_alive_is_not_crashed(self):
        """A lapsed lease whose holder's process is still alive is `lapsed_alive`, never
        `crashed`: no expiry is recorded while the process lives (research row f)."""
        lane = H.Lane(
            "T1", "a", False, 1000.0, True, renewed_at=900.0, lapsed_alive=True, live_pids=(123,)
        )
        found = H.inspect([lane], [], [], [], 2000.0, _limits())
        kinds = [f.kind for f in found]
        assert "lapsed_alive" in kinds, kinds
        assert "crashed" not in kinds, kinds
        la = next(f for f in found if f.kind == "lapsed_alive")
        assert la.evidence["pids"] == [123]
        assert "claim T1" in la.fix

    def test_lapsed_and_gone_is_crashed(self):
        """A lapsed lease with no live process still reports CRASHED (work at risk)."""
        lane = H.Lane("T1", "a", False, 1000.0, True)
        found = H.inspect([lane], [], [], [], 2000.0, _limits())
        assert any(f.kind == "crashed" for f in found)
        assert not any(f.kind == "lapsed_alive" for f in found)


class TestReadyToLand:
    def test_finished_but_not_merged_is_ready_to_land(self):
        """Gates done except merge, commits ahead of the base: only merge is owed (row b)."""
        r = H.Ready("T1", "a", commits_ahead=3, owed=("merge",), branch="b")
        found = H.inspect([], [], [], [], 0.0, _limits(), [], ready=[r])
        (f,) = [f for f in found if f.kind == "ready_to_land"]
        assert f.evidence["commits_ahead"] == 3
        assert f.evidence["owed"] == ["merge"]
        assert "merge" in f.fix

    def test_more_than_merge_owed_is_not_ready_to_land(self):
        """A lane that still owes `unit_tests` beyond `merge` is not proposed to land: the
        judge filters any Ready whose owed gates are not exactly merge-shaped."""
        r = H.Ready("T1", "a", commits_ahead=1, owed=("unit_tests", "merge"))
        found = H.inspect([], [], [], [], 0.0, _limits(), [], ready=[r])
        assert [f.kind for f in found if f.kind == "ready_to_land"] == []

    def test_zero_commits_ahead_is_not_ready_to_land(self):
        """Nothing ahead of the base is nothing to land: no READY_TO_LAND for 0 commits."""
        found = H.inspect([], [], [], [], 0.0, _limits(), [], ready=[H.Ready("T1", "a", 0, ())])
        assert [f.kind for f in found if f.kind == "ready_to_land"] == []


class TestMergeInterrupted:
    def test_mid_flight_merge(self):
        """MERGE_HEAD set (or unmerged paths) in the item's tree: a merge mid-flight (row h)."""
        m = H.MergeHang("T1", "a", tree="/wt/T1", merging=True)
        found = H.inspect([], [], [], [], 0.0, _limits(), [], merges=[m])
        (f,) = [f for f in found if f.kind == "merge_interrupted"]
        assert f.evidence["merging"] is True
        assert not f.evidence["landed_unlogged"]

    def test_landed_but_not_logged(self):
        """The branch head is on the base but no merge event was logged: SAFE_AUTO records it."""
        m = H.MergeHang("T1", "a", landed_unlogged=True, head="abc123", base="def456")
        found = H.inspect([], [], [], [], 0.0, _limits(), [], merges=[m])
        (f,) = [f for f in found if f.kind == "merge_interrupted"]
        assert f.evidence["landed_unlogged"] is True
        assert f.evidence["head"] == "abc123"

    def test_merging_takes_precedence_over_landed_unlogged(self):
        """A mid-flight merge beats a landed-unlogged tag: never advise 'record merge'
        while MERGE_HEAD is still set."""
        m = H.MergeHang("T1", "a", tree="/wt", merging=True, landed_unlogged=True, head="x", base="y")
        found = H.inspect([], [], [], [], 0.0, _limits(), [], merges=[m])
        (f,) = [f for f in found if f.kind == "merge_interrupted"]
        assert f.evidence["merging"] is True
        assert "re-merge" not in f.fix.lower() or "abort" in f.fix


class TestBootId:
    def test_first_sighting_is_a_change(self, tmp_path: Path):
        """The first time ddflow sees this machine it records the boot id, which counts as a
        change from nothing (row e: the stored value is what the next pass compares)."""
        boot = W.boot_id()
        if not boot:
            return  # no /proc: nothing is provable here
        out = W.observe_boot(tmp_path)
        assert out["changed"] is True
        assert W.stored_boot(tmp_path) == boot

    def test_unchanged_boot_is_not_a_change(self, tmp_path: Path):
        boot = W.boot_id()
        if not boot:
            return
        first = W.observe_boot(tmp_path)
        second = W.observe_boot(tmp_path)
        assert first["changed"] is True
        assert second["changed"] is False
        assert second["previous"] == boot
        assert second["boot_id"] == boot
