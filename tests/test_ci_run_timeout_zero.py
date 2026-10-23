"""B96ac0c2bf8: `ci run` takes `timeout_s=0` as "no per-run limit" (the merge queue passes its own,
possibly 0); only a real per-run timeout is bounded by `[gates].max_run_timeout_s`."""

from __future__ import annotations

from ddflow.api import cirun


def test_a_zero_timeout_is_not_refused_as_out_of_bounds(repo):
    out = cirun.run(repo, ref="HEAD", timeout_s=0, command="true")
    assert "outside 1.." not in (out.reason or "")


def test_a_real_timeout_beyond_the_bound_still_is(repo):
    out = cirun.run(repo, ref="HEAD", timeout_s=10**9, command="true")
    assert out.exit == 3 and "outside 1.." in out.reason
    assert "outside 1.." in cirun.run(repo, ref="HEAD", timeout_s=-5, command="true").reason
