"""`ddflow metrics locks`: the lease-cost report on a seeded log with known durations."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lanes_fixture import T0, Log
from metrics_fixture import frozen

from ddflow.services import metrics as M

R = "rubber_duck"


class Fake:
    """Probes with canned answers: files per (before, after) and conflicts per head."""

    def __init__(self, files, conflicts):
        self._files, self._conflicts = files, conflicts

    def files(self, before, after):
        return self._files.get((before, after))

    def conflicts(self, base, head):
        return self._conflicts.get(head)


def merged(log: Log, item: str, t: float, tag: str) -> None:
    log.add(
        "worktree.merged", item, "a", t, sha=tag, branch=f"b-{tag}",
        landed_before=f"{tag}0", landed_after=f"{tag}1", branch_head=f"{tag}h",
    )  # fmt: skip


def seeded() -> Log:
    """A: claimed T0, research 100..300, review 400..700, merged 800, released 1000.
    B: wanted A's glob at T0+50, waited to T0+1000 (files disjoint from A's), merged 1500.
    C: wanted A's glob at T0+60 and waited behind it too, but shares a file with it."""
    log = Log()
    for t in "ABC":
        log.task(t, "src/*.py")
    log.claim("a", "A", T0, "src/*.py,docs/x.md")
    log.add("gate.started", "A", "a", T0 + 100, gate="research")
    log.add("gate.passed", "A", "a", T0 + 300, gate="research", by="a", evidence={})
    log.add(
        "gate.passed", "A", "a", T0 + 700, gate=R, by="r", evidence={"model": "m", "elapsed_s": 300}
    )
    merged(log, "A", T0 + 800, "A")
    log.complete("a", "A", T0 + 900)
    log.release("a", "A", T0 + 1000)
    log.add("lease.waiting", "B", "b", T0 + 50, at=T0 + 50, globs=["src/*.py"], holder="a", on=[])
    log.claim("b", "B", T0 + 1000, "src/*.py")
    merged(log, "B", T0 + 1500, "B")
    log.add("bug.found", "B1", "b", T0 + 1600, item="B", title="red")
    log.release("b", "B", T0 + 1700)
    log.add("lease.waiting", "C", "c", T0 + 60, at=T0 + 60, globs=["src/*.py"], holder="a", on=[])
    log.claim("c", "C", T0 + 1000, "src/*.py")
    merged(log, "C", T0 + 1600, "C")
    log.release("c", "C", T0 + 1800)
    return log


FILES = {
    ("A0", "A1"): ["src/a.py", "docs/x.md", "z.txt"],
    ("B0", "B1"): ["src/b.py"],
    ("C0", "C1"): ["src/a.py"],
}
CONFLICTS = {"Ah": False, "Bh": False, "Ch": True}


def run(log=None, probes="fake", **knobs):
    st, cfg, now = frozen(log or seeded())
    cfg.metrics.min_samples = 1
    for k, v in knobs.items():
        setattr(cfg.metrics, k, v)
    p = Fake(FILES, CONFLICTS) if probes == "fake" else None
    return M.run((log or seeded()).events, st, cfg, now, view="locks", probes=p)


def test_hold_is_split_into_its_parts():
    s = run().metrics["hold"]["stages"]
    # A is the first lease: hold 900 (completion ends it), 100 to the first gate, 200 gate + 300
    # review, 200 from the review to the end, 100 after the merge; the other leases have no gate runs.
    assert s["hold"]["n"] == 3
    assert s["claim_to_first_gate"]["n"] == 1 and s["claim_to_first_gate"][
        "p50_s"
    ] == pytest.approx(100)
    assert s["in_gates"]["p50_s"] == pytest.approx(200)
    assert s["in_reviews"]["p50_s"] == pytest.approx(300)
    assert s["last_review_to_release"]["p50_s"] == pytest.approx(200)
    assert s["after_merge"]["n"] == 3 and s["after_merge"]["p50_s"] == pytest.approx(200)


def test_after_merge_share_of_hold():
    share = run().metrics["hold"]["after_merge_share"]
    assert share == pytest.approx((100 + 200 + 200) / (900 + 700 + 800))


def test_waits_and_false_serialization_use_what_was_written():
    w = run().metrics["waits"]
    assert w["spans"]["n"] == 2 and w["total_s"] == pytest.approx(950 + 940)
    assert w["classified"] == 2
    fs = w["false_serialization"]
    assert fs["spans"] == 1 and fs["seconds"] == pytest.approx(950)  # B disjoint from A, C is not
    assert fs["share_of_classified_seconds"] == pytest.approx(950 / 1890)


def test_declared_versus_written():
    d = run().metrics["declared"]
    assert d["items"] == 3
    # A declared 2 globs, both touched; B 1 glob touched; C 1 glob touched
    assert d["declared_untouched_share"] == pytest.approx(0.0)
    # A wrote z.txt outside (1/3); B none; C only src/a.py
    assert d["actual_outside_declared_share"] == pytest.approx((1 / 3 + 0 + 0) / 3)
    assert d["items_with_undeclared_writes"] == 1


def test_conflicts_and_red_main_rates_and_falsifier():
    m = run().metrics
    assert m["merges"]["conflicts"] == 1 and m["merges"]["conflict_rate"] == pytest.approx(1 / 3)
    assert m["merges"]["red_main"] == 1 and m["merges"]["red_main_rate"] == pytest.approx(1 / 3)
    f = m["falsifiers"]
    assert f["tripped"] == ["textual_conflicts", "red_main"]
    assert f["revert"]["cli"] == "ddflow config --set locks.mode strict"
    assert f["revert"]["mcp"] == "ddflow_configure set=locks.mode value=strict"
    assert "ddflow config --set locks.mode strict" in run().text


def test_falsifier_holds_below_the_knobs_and_window_is_a_knob():
    ans = run(lock_falsifier_conflict_pct=50, lock_falsifier_red_main_pct=50)
    assert ans.metrics["falsifiers"]["tripped"] == []
    assert run(lock_red_main_window_s=10).metrics["merges"]["red_main"] == 0
    assert run(lock_probe_max=0).metrics["merges"]["conflict_rate"] is None


def test_without_git_the_git_figures_are_unavailable_not_zero():
    ans = run(probes=None)
    m = ans.metrics
    assert m["merges"]["conflict_rate"] is None and m["merges"]["probed"] == 0
    assert m["declared"]["declared_untouched_share"] is None
    assert m["waits"]["false_serialization"]["share_of_classified_seconds"] is None
    assert "git" in m["unavailable"] and "conflict_rate" in m["unavailable"]
    assert m["falsifiers"]["rows"]["textual_conflicts"]["state"] == "unknown"
    assert m["falsifiers"]["tripped"] == ["red_main"]
    assert ans.ok  # the log-only figures still stand


def test_probe_that_could_not_run_is_counted_unavailable():
    log = seeded()
    st, cfg, now = frozen(log)
    cfg.metrics.min_samples = 1
    probes = Fake(FILES, {"Ah": None, "Bh": False, "Ch": False})
    m = M.run(log.events, st, cfg, now, view="locks", probes=probes).metrics["merges"]
    assert m["probe_unavailable"] == 1 and m["conflicts"] == 0
    assert m["conflict_rate"] == 0.0  # of the two that answered


def test_minimum_samples_withholds_figures():
    ans = run(min_samples=10)
    assert not ans.ok and "not enough data" in ans.reason
    assert ans.metrics["hold"]["stages"]["hold"]["p50_s"] is None


def test_suggestion_figures_are_named_not_measured():
    nm = run().metrics["not_measured"]
    assert set(nm) == {"suggestion_accept_rate", "suggestion_answer_time", "ci_red_after_apply"}
    assert "locks" in M.VIEWS


def _git(repo, *args):
    import subprocess

    out = subprocess.run(
        ["git", "-C", str(repo), "-c", "commit.gpgsign=false", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return out.stdout.strip()


def test_git_probes_read_files_and_conflicts_through_infra_git(tmp_path):
    from ddflow.services.metrics.lockgit import GitProbes

    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "f.txt").write_text("1\n2\n3\n")
    _git(tmp_path, "add", "."), _git(tmp_path, "commit", "-qm", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "checkout", "-qb", "side")
    (tmp_path / "f.txt").write_text("1\nside\n3\n")
    _git(tmp_path, "commit", "-qam", "side")
    side = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "checkout", "-q", "main")
    (tmp_path / "f.txt").write_text("1\nmain\n3\n")
    (tmp_path / ".ddflow").mkdir()
    (tmp_path / ".ddflow" / "e").write_text("x")
    _git(tmp_path, "add", "-f", "."), _git(tmp_path, "commit", "-qm", "main")
    main = _git(tmp_path, "rev-parse", "HEAD")
    p = GitProbes(tmp_path)
    assert p.files(base, main) == ["f.txt"]  # ddflow's own state is not a written file
    assert p.conflicts(main, side) is True and p.conflicts(base, side) is False
    assert p.conflicts(main, "0" * 40) is None and p.files("", main) is None


def test_declared_counts_an_item_with_any_merge_inside_the_window():
    log = seeded()
    merged(log, "A", T0 + 1900, "A2")  # A's first merge (T0+800) is before the window, this one in
    st, cfg, now = frozen(log)
    cfg.metrics.min_samples = 1
    probes = Fake({**FILES, ("A20", "A21"): ["src/a.py"]}, CONFLICTS)
    since = f"{int(now - (T0 + 1000))}s"
    ans = M.run(log.events, st, cfg, now, view="locks", since=since, probes=probes)
    assert ans.metrics["declared"]["items"] == 3


def test_declared_globs_come_from_the_lease_held_at_the_merge():
    log = Log()
    log.task("A", "x")
    log.claim("a", "A", T0, "old/*.py")
    log.release("a", "A", T0 + 100)
    log.claim("a", "A", T0 + 200, "src/*.py")
    merged(log, "A", T0 + 300, "A")
    log.release("a", "A", T0 + 400)
    st, cfg, now = frozen(log)
    cfg.metrics.min_samples = 1
    probes = Fake({("A0", "A1"): ["src/a.py"]}, {})
    d = M.run(log.events, st, cfg, now, view="locks", probes=probes).metrics["declared"]
    assert d["actual_outside_declared_share"] == pytest.approx(0.0)
    assert d["declared_untouched_share"] == pytest.approx(0.0)


def test_declared_is_scored_on_the_in_window_merge_only():
    log = Log()
    log.task("A", "x")
    log.claim("a", "A", T0, "old/*.py")
    merged(log, "A", T0 + 100, "A")
    log.release("a", "A", T0 + 150)
    log.claim("a", "A", T0 + 200, "src/*.py")
    merged(log, "A", T0 + 1900, "B")
    log.release("a", "A", T0 + 2000)
    st, cfg, now = frozen(log)
    cfg.metrics.min_samples = 1
    probes = Fake({("A0", "A1"): ["stray.py"], ("B0", "B1"): ["src/a.py"]}, {})
    # the out-of-window merge wrote stray.py outside old/*.py: scoring it would show an undeclared write
    since = f"{int(now - (T0 + 1000))}s"
    d = M.run(log.events, st, cfg, now, view="locks", since=since, probes=probes).metrics[
        "declared"
    ]
    assert d["items"] == 1 and d["actual_outside_declared_share"] == pytest.approx(0.0)


def test_each_merge_is_scored_against_its_own_lease():
    log = Log()
    log.task("A", "x")
    log.claim("a", "A", T0, "src/*.py")
    merged(log, "A", T0 + 1900, "A")
    log.release("a", "A", T0 + 2000)
    log.claim("a", "A", T0 + 2100, "docs/*.md")
    merged(log, "A", T0 + 2200, "B")
    log.release("a", "A", T0 + 2300)
    st, cfg, now = frozen(log)
    cfg.metrics.min_samples = 1
    probes = Fake({("A0", "A1"): ["src/a.py"], ("B0", "B1"): ["src/c.py"]}, {})
    since = f"{int(now - (T0 + 1000))}s"
    d = M.run(log.events, st, cfg, now, view="locks", since=since, probes=probes).metrics[
        "declared"
    ]
    assert d["actual_outside_declared_share"] == pytest.approx(
        0.5
    )  # src/c.py was not under docs/*.md
    assert d["items_with_undeclared_writes"] == 1


def test_false_serialization_uses_the_files_of_the_leases_that_collided():
    log = Log()
    for t in "AB":
        log.task(t, "src/*.py")
    log.claim("a", "A", T0 - 500, "src/*.py")
    merged(log, "A", T0 - 450, "A1")
    log.release("a", "A", T0 - 400)
    log.claim("a", "A", T0, "src/*.py")
    merged(log, "A", T0 + 900, "A2")
    log.release("a", "A", T0 + 1000)
    log.add("lease.waiting", "B", "b", T0 + 50, at=T0 + 50, globs=["src/*.py"], holder="a", on=[])
    log.claim("b", "B", T0 + 1000, "src/*.py")
    merged(log, "B", T0 + 1500, "B2")
    log.release("b", "B", T0 + 1600)
    log.claim("b", "B", T0 + 2000, "src/*.py")  # a later lease of B also touches common.txt
    merged(log, "B", T0 + 2100, "B1")
    log.release("b", "B", T0 + 2200)
    files = {
        ("A10", "A11"): ["common.txt"], ("B10", "B11"): ["common.txt"],
        ("A20", "A21"): ["src/a.py"], ("B20", "B21"): ["src/b.py"],
    }  # fmt: skip
    st, cfg, now = frozen(log)
    cfg.metrics.min_samples = 1
    w = M.run(log.events, st, cfg, now, view="locks", probes=Fake(files, {})).metrics["waits"]
    assert w["classified"] == 1 and w["false_serialization"]["spans"] == 1


def test_merges_before_the_window_are_not_counted():
    log = seeded()
    st, cfg, now = frozen(log)
    cfg.metrics.min_samples = 1
    since = f"{int(now - (T0 + 850))}s"  # A's merge (T0+800) is before the window, B and C are in
    w = M.run(log.events, st, cfg, now, view="locks", since=since, probes=Fake(FILES, CONFLICTS))
    assert w.metrics["merges"]["merges"] == 2


def test_a_blocker_that_arrived_mid_wait_is_counted():
    log = Log()
    for t in "ABC":
        log.task(t, "x")
    log.claim("a", "A", T0, "src/*.py")
    merged(log, "A", T0 + 400, "A")
    log.release("a", "A", T0 + 500)
    log.add("lease.waiting", "B", "b", T0 + 10, at=T0 + 10, globs=["src/*.py"], holder="a", on=[])
    log.claim("c", "C", T0 + 200, "src/*.py")  # only arrives mid-wait
    merged(log, "C", T0 + 450, "C")
    log.release("c", "C", T0 + 600)
    log.claim("b", "B", T0 + 700, "src/*.py")
    merged(log, "B", T0 + 800, "B")
    log.release("b", "B", T0 + 900)
    files = {("A0", "A1"): ["src/x.py"], ("C0", "C1"): ["src/b.py"], ("B0", "B1"): ["src/b.py"]}
    st, cfg, now = frozen(log)
    cfg.metrics.min_samples = 1
    w = M.run(log.events, st, cfg, now, view="locks", probes=Fake(files, {})).metrics["waits"]
    assert w["classified"] == 1 and w["false_serialization"]["spans"] == 0  # C overlapped B


def test_a_merge_at_the_instant_the_lease_ends_belongs_to_that_lease():
    log = Log()
    for t in "AB":
        log.task(t, "x")
    log.claim("a", "A", T0, "src/*.py")
    log.add("lease.waiting", "B", "b", T0 + 10, at=T0 + 10, globs=["src/*.py"], holder="a", on=[])
    merged(log, "A", T0 + 500, "A")
    log.release("a", "A", T0 + 500)  # merged and released in the same instant
    log.claim("b", "B", T0 + 600, "src/*.py")
    merged(log, "B", T0 + 700, "B")
    log.release("b", "B", T0 + 800)
    files = {("A0", "A1"): ["src/a.py"], ("B0", "B1"): ["src/b.py"]}
    st, cfg, now = frozen(log)
    cfg.metrics.min_samples = 1
    w = M.run(log.events, st, cfg, now, view="locks", probes=Fake(files, {})).metrics["waits"]
    assert w["classified"] == 1 and w["false_serialization"]["spans"] == 1


def test_gitprobes_conflicts_routes_through_the_probe(monkeypatch):
    """GitProbes.conflicts delegates to probe.run so the two cannot drift (clean->False,
    conflict->True, unavailable->None); the empty-ref guard short-circuits before any git call."""
    from ddflow.services import probe as P
    from ddflow.services.metrics import lockgit

    def fake_run(repo, item, against, *, timeout_s, min_git="2.38"):
        fake_run.calls.append((item, against, timeout_s, min_git))
        return P.Probe(fake_run.verdict, P.MERGE_TREE, (), item, against)

    fake_run.calls = []
    monkeypatch.setattr(lockgit, "probe_run", fake_run)
    gp = lockgit.GitProbes(Path("."), timeout_s=7, min_git="2.43")

    fake_run.verdict = P.CLEAN
    assert gp.conflicts("b", "h") is False
    fake_run.verdict = P.CONFLICT
    assert gp.conflicts("b", "h") is True
    fake_run.verdict = P.UNAVAILABLE
    assert gp.conflicts("b", "h") is None
    assert fake_run.calls == [("b", "h", 7, "2.43")] * 3

    fake_run.calls = []
    assert gp.conflicts("", "h") is None and gp.conflicts("b", "") is None
    assert fake_run.calls == []  # no refs: never touched git
