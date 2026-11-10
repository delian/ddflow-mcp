"""A measured baseline-red partial is accepted without a person (D-baseline-red-auto-accept).

The fixture is `test_gate_baseline_red`'s: a fake pytest that fails ``test_a`` on the base too.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from test_gate_baseline_red import ID, _project

from ddflow.config import Config
from ddflow.core.handlers.asks import asks_of
from ddflow.core.model import fold
from ddflow.core.records import GateRecord
from ddflow.infra.log import EventLog
from ddflow.services import gate_accept as GA
from ddflow.services import inbox as IB
from ddflow.services import testscope as TSC
from ddflow.services.gates import baseline as BL
from ddflow.services.gates import baseline_red as BR
from ddflow.services.gates.defs import GateDef


def _state(repo):
    return fold(EventLog(repo).read_all(), strict=False)


def _cfg(repo):
    return Config.load(repo)


def _run(repo):
    _code, out, _e = run_cli(repo, "--json", "gate", "run", "T1", "unit_tests", agent="worker")
    return json.loads(out)


def test_a_subset_of_the_baseline_failures_is_accepted_and_told_once(repo) -> None:
    _project(repo)
    body = _run(repo)
    assert body["outcome"] == "partial"
    st = _state(repo)
    it = st.items["T1"]
    auto = it.gates["unit_tests"].evidence["baseline"]["auto"]
    assert auto["accepted"] is True and auto["ids"] == [ID]
    got = GA.accepted(st, it, "unit_tests", _cfg(repo))
    assert got.ok and "accepted-baseline-red" in got.reason
    assert it.gate_outcome("unit_tests") == "partial"  # never passed
    assert not GA.accepted(st, it, "unit_tests").ok  # no cfg: only a person
    bug = st.bugs[it.gates["unit_tests"].evidence["baseline"]["bug"]]
    assert "baseline-red" in (getattr(bug, "tags", None) or [])
    notices = [r for r in __import__("ddflow.core.handlers.asks", fromlist=["x"]).asks_of(st).values()
               if r["answered_by"] == "policy:baseline_red_auto"]  # fmt: skip
    assert len(notices) == 1 and ID in notices[0]["text"]
    msgs = IB.pending(repo, "coordinator")
    assert len(msgs) == 1 and ID in msgs[0].text
    bugs = set(_state(repo).bugs)
    _run(repo)  # the same measurement again files and says nothing more
    again = _state(repo)
    assert set(again.bugs) == bugs
    assert len(IB.pending(repo, "coordinator")) == 1
    asked = [r for r in asks_of(again).values() if r["answered_by"] == "policy:baseline_red_auto"]
    assert len(asked) == 1


def test_the_knob_off_leaves_it_to_a_person(repo) -> None:
    _project(repo)
    run_cli(repo, "config", "gates.baseline_red_auto", "off")
    assert _run(repo)["outcome"] == "partial"
    st = _state(repo)
    assert not GA.accepted(st, st.items["T1"], "unit_tests", _cfg(repo)).ok
    assert IB.pending(repo, "coordinator") == []


def test_a_failure_in_a_file_the_item_changed_is_not_accepted(repo) -> None:
    _project(repo)
    (repo / "tests" / "test_x.py").write_text("def test_a():\n    assert True\n")
    assert _run(repo)["outcome"] == "partial"
    st = _state(repo)
    auto = st.items["T1"].gates["unit_tests"].evidence["baseline"]["auto"]
    assert auto["accepted"] is False and "tests/test_x.py" in auto["why"]
    assert not GA.accepted(st, st.items["T1"], "unit_tests", _cfg(repo)).ok


def test_a_whole_tree_guard_selected_by_governing_does_not_block_auto_accept(
    repo, monkeypatch
) -> None:
    """B-baseline-reach-blocks-red-main-fix: an item whose ci selects the architecture guards
    only because they declare ``GOVERNS = ("ddflow/**", ...)`` -- the whole tree -- did not
    cause a guard that also fails on the base. The guard's test file is reached trivially, so
    its presence in the selection must not block auto-accept."""
    from ddflow.services.testselect import Selected, Selection

    sel = Selection(
        base="main",
        changed=["ddflow/api/family_schema.py"],
        tests=[Selected("tests/test_architecture_guards.py", "governs ddflow/**", 1)],
    )
    monkeypatch.setattr(BL.TS, "select", lambda tree, base, **kw: sel)
    cfg = SimpleNamespace(gates=SimpleNamespace(baseline_red_auto="on"))
    fid = "tests/test_architecture_guards.py::test_ratchet"
    auto = BL.auto_decision(cfg, repo, "main", [fid], {"red": True}, {})
    assert auto["accepted"] is True, auto


def test_a_whole_tree_guard_the_item_changed_still_blocks_auto_accept(repo, monkeypatch) -> None:
    from ddflow.services.testselect import Selected, Selection

    sel = Selection(
        base="main",
        changed=["tests/test_architecture_guards.py"],
        tests=[Selected("tests/test_architecture_guards.py", "changed", 0)],
    )
    monkeypatch.setattr(BL.TS, "select", lambda tree, base, **kw: sel)
    cfg = SimpleNamespace(gates=SimpleNamespace(baseline_red_auto="on"))
    fid = "tests/test_architecture_guards.py::test_ratchet"
    auto = BL.auto_decision(cfg, repo, "main", [fid], {"red": True}, {})
    assert auto["accepted"] is False, auto
    assert "test_architecture_guards.py" in auto["why"]


def test_a_hub_module_change_does_not_block_auto_accept_when_all_measured(
    repo, monkeypatch
) -> None:
    """B0f41a2f1d6: a hub-module change cuts the reach walk (Selection.full), so which test
    files it reaches by import or governing cannot be told -- but `red` means every failing
    test was measured on the base (its `unmeasured` is empty) and `uncompared` being empty
    means each failure text was compared whole-vs-whole, so the reach is not needed to
    attribute them. Under `full` nothing reads `sel.tests`/`sel.cut` (the walk that would fill
    them was cut), so this accepts on `sel.changed` and the measurement alone."""
    from ddflow.services.testselect import Selection

    sel = Selection(
        base="main",
        changed=["ddflow/services/gates/__init__.py"],
        full="a hub module changed: ddflow.services.gates (150 importing files)",
    )
    monkeypatch.setattr(BL.TS, "select", lambda tree, base, **kw: sel)
    cfg = SimpleNamespace(gates=SimpleNamespace(baseline_red_auto="on"))
    fid = "tests/test_ci_gate.py::test_live"
    auto = BL.auto_decision(
        cfg, repo, "main", [fid], {"red": True, "unmeasured": [], "uncompared": []}, {}
    )
    assert auto["accepted"] is True, auto


def test_a_hub_module_change_plus_a_changed_failing_test_still_declines(repo, monkeypatch) -> None:
    """A hub cut the reach AND the item changed a failing test FILE itself -- the changed-file
    list is still known under `full`, so auto-accept must still decline, exactly as the reach
    check would if it could run."""
    from ddflow.services.testselect import Selection

    sel = Selection(
        base="main",
        changed=[
            "ddflow/services/gates/__init__.py",
            "tests/test_ci_gate.py",
        ],
        full="a hub module changed: ddflow.services.gates (150 importing files)",
    )
    monkeypatch.setattr(BL.TS, "select", lambda tree, base, **kw: sel)
    cfg = SimpleNamespace(gates=SimpleNamespace(baseline_red_auto="on"))
    fid = "tests/test_ci_gate.py::test_live"
    auto = BL.auto_decision(
        cfg, repo, "main", [fid], {"red": True, "unmeasured": [], "uncompared": []}, {}
    )
    assert auto["accepted"] is False, auto
    assert "import reach cannot be told" in auto["why"]


def test_a_hub_module_change_with_an_uncompared_failure_still_declines(repo, monkeypatch) -> None:
    """A failing id whose failure text is missing on one side is treated by `_differs` as
    "not different" (an absence of evidence, not a match), so under `full` it must still
    decline: `red` alone cannot prove it failed the same way (roborev finding on B0f41a2f1d6)."""
    from ddflow.services.testselect import Selection

    sel = Selection(
        base="main",
        changed=["ddflow/services/gates/__init__.py"],
        full="a hub module changed: ddflow.services.gates (150 importing files)",
    )
    monkeypatch.setattr(BL.TS, "select", lambda tree, base, **kw: sel)
    cfg = SimpleNamespace(gates=SimpleNamespace(baseline_red_auto="on"))
    fid = "tests/test_ci_gate.py::test_live"
    auto = BL.auto_decision(
        cfg, repo, "main", [fid], {"red": True, "unmeasured": [], "uncompared": [fid]}, {}
    )
    assert auto["accepted"] is False, auto
    assert "import reach cannot be told" in auto["why"]


def test_a_hub_module_change_without_the_uncompared_field_still_declines(repo, monkeypatch) -> None:
    """`found` that LACKS `uncompared` must decline, never read as "all compared": the
    whole-vs-whole guarantee is absent, not empty, so the reach cannot be told."""
    from ddflow.services.testselect import Selection

    sel = Selection(
        base="main",
        changed=["ddflow/services/gates/__init__.py"],
        full="a hub module changed: ddflow.services.gates (150 importing files)",
    )
    monkeypatch.setattr(BL.TS, "select", lambda tree, base, **kw: sel)
    cfg = SimpleNamespace(gates=SimpleNamespace(baseline_red_auto="on"))
    fid = "tests/test_ci_gate.py::test_live"
    auto = BL.auto_decision(cfg, repo, "main", [fid], {"red": True, "unmeasured": []}, {})
    assert auto["accepted"] is False, auto
    assert "import reach cannot be told" in auto["why"]


def test_the_base_is_run_once_per_sha_and_shared(repo) -> None:
    _project(repo)
    calls: list[str] = []

    def run(gdef, cwd):
        calls.append(gdef.command)
        # The failure text is what the cache hangs the round-trip on: an entry with no text
        # is re-run to spot a new offender, so without it the 2nd call would not hit "cached".
        return "failed", {"failed_tests": [ID], "failed_reasons": {ID: "assert 1 == 2"}}

    gdef = GateDef(id="unit_tests", command="pytest -q tests/")
    a = BR.on_base(repo, "main", gdef, [ID], run, 3600)
    b = BR.on_base(repo, "main", gdef, [ID], run, 3600)
    assert a["red"] and b["red"] and b.get("cached") and len(calls) == 1
    assert (
        BR.on_base(repo, "main", gdef, [ID], run, 0)["red"] and len(calls) == 2
    )  # ttl 0: no cache


def test_a_run_that_could_not_run_is_not_cached_and_never_red(repo) -> None:
    _project(repo)
    gdef = GateDef(id="unit_tests", command="pytest -q tests/")
    got = BR.on_base(repo, "main", gdef, [ID], lambda g, c: ("unavailable", {}), 3600)
    assert got["red"] is False
    sha = got["base_sha"]
    assert BL.cache_load(repo, sha, 3600) == {}


def test_an_unavailable_or_failed_gate_is_never_accepted() -> None:
    cfg = SimpleNamespace(gates=SimpleNamespace(baseline_red_auto="on"))
    for outcome in ("unavailable", "failed", "passed"):
        ev = {"baseline": {"red": True, "auto": {"accepted": True, "ids": [ID]}}}
        rec = GateRecord("unit_tests", outcome, "t", "a", "r", ev)
        it = SimpleNamespace(
            id="T1", gates={"unit_tests": rec}, gate_outcome=lambda g, o=outcome: o
        )
        assert GA.auto_ids(it, "unit_tests", cfg) == []


def test_ci_partial_baseline_red_does_not_stop_unit_tests() -> None:
    ev = {"baseline": {"red": True}}
    ci = GateRecord("ci", "partial", "t", "a", "baseline-red", ev)
    assert TSC._ci_baseline_red(SimpleNamespace(gates={"ci": ci}))
    ci = GateRecord("ci", "partial", "t", "a", "partial", {})
    assert not TSC._ci_baseline_red(SimpleNamespace(gates={"ci": ci}))
    assert not TSC._ci_baseline_red(SimpleNamespace(gates={}))


def test_an_open_bug_naming_the_ids_is_linked_not_refiled(repo) -> None:
    from ddflow.api import gate_baseline as GB

    _project(repo)
    run_cli(repo, "bug", "found", "--summary", f"{ID} fails on main", agent="worker")
    bug = next(iter(_state(repo).bugs))
    assert GB._linked(repo, "worker", [ID]) == bug
    assert GB._linked(repo, "worker", ["tests/other.py::t"]) == ""
    assert GB._names(f"see {ID}: fails.", ID) and GB._names(f"[{ID}] red", ID)
    assert not GB._names(f"{ID}_b is red", ID) and not GB._names(f"{ID}/helpers.py", ID)
    assert GB._linked(repo, "worker", [ID[:-1]]) == ""  # the start of a longer id is not the id


def test_a_run_whose_ids_are_each_owned_by_a_bug_is_linked_not_refiled(repo) -> None:
    """B-baseline-red-filer-piles-up: `_linked` deduped only when ONE open bug named every id,
    so a run whose failing set differs by a single test filed a whole new bug even though each
    id already had its own open bug. An id the set of open bugs already names (any one of them)
    is owned; only ids no open bug names are new."""
    from ddflow.api import gate_baseline as GB

    _project(repo)
    a = ID
    b = "tests/test_y.py::test_b"
    run_cli(repo, "bug", "found", "--summary", f"{a} fails on main", agent="worker")
    run_cli(repo, "bug", "found", "--summary", f"{b} fails on main", agent="worker")
    st = _state(repo)
    assert len(st.bugs) == 2
    # A single bug naming the single id (the old case) still links.
    assert GB._linked(repo, "worker", [a]) in st.bugs
    # Each id owned by a DIFFERENT open bug: the set is still fully owned -- nothing new.
    got = GB._linked(repo, "worker", [a, b])
    assert got, "a set whose ids each already have an open bug must not read as unlinked"
    assert got in st.bugs
    # An id no open bug names leaves the set new.
    c = "tests/test_z.py::test_c"
    assert GB._linked(repo, "worker", [a, c]) == ""


def _ci_project(repo, n: int, *, extra_fail: str = "") -> None:
    """A project whose `ci` gate runs `ddflow ci run` over a fake pytest failing ``n`` tests on
    the base too (and ``extra_fail`` only in the item's tree)."""
    run_cli(repo, "init")
    (repo / "bin").mkdir()
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text("def test_a():\n    pass\n")
    extra = (
        f"if os.path.exists(os.path.join(here, '..', 'newfail')): names.append({extra_fail!r})\n"
        if extra_fail
        else ""
    )
    (repo / "bin" / "pytest").write_text(
        "#!/usr/bin/env python3\nimport os, sys\n"
        "here = os.path.dirname(os.path.abspath(__file__))\n"
        f"every = [f'tests/test_big.py::test_{{i:03d}}' for i in range({n})]\n"
        "asked = [a for a in sys.argv[1:] if '::' in a]\n"
        "names = [a for a in asked if a in every] if asked else list(every)\n"
        + extra
        + "print('=== short test summary info ===')\n"
        "for t in names:\n    print(f'FAILED {t} - boom')\n"
        "print(f'{len(names)} failed in 0.1s')\nsys.exit(1 if names else 0)\n"
    )
    (repo / "bin" / "pytest").chmod(0o755)
    with (repo / ".ddflow" / "config.toml").open("a") as fh:
        fh.write('\n[ci]\ncommand = "./bin/pytest -q tests/"\ntests_scope = "full"\n')
    (repo / ".ddflow" / "gates.toml").write_text(
        '[gate.unit_tests]\ncommand = "./bin/pytest -q tests/"\ncwd = "repo"\n'
    )
    run_cli(repo, "config", "worktree.enabled", "false")
    import subprocess

    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "project"], check=True)
    code, _o, err = run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
    assert code == 0, err
    code, _o, err = run_cli(repo, "claim", "T1", "--no-worktree", agent="worker")
    assert code == 0, err


def test_ci_red_only_from_a_large_baseline_failing_list_is_partial(repo) -> None:
    from ddflow.services.gates import baseline as BL

    _ci_project(repo, 120)  # more than the evidence keeps
    run_cli(repo, "--json", "gate", "run", "T1", "ci", agent="worker")
    it = _state(repo).items["T1"]
    ci = it.gates["ci"]
    assert ci.outcome == "partial", (ci.outcome, ci.reason)
    base = ci.evidence["baseline"]
    assert base["red"] is True and len(base["ids"]) == 120
    assert base["auto"]["accepted"] is True
    stub = ci.evidence["failing_ids"]
    assert len(BL.read_full(repo, stub)) == 120
    # unit_tests is not held back by a partial baseline-red ci
    out = json.loads(run_cli(repo, "--json", "gate", "run", "T1", "unit_tests", agent="worker")[1])
    assert "refusal" not in out, out


def test_the_whole_failing_list_is_linked_from_the_ci_record_as_a_blob(repo) -> None:
    from ddflow.core import blobref as B
    from ddflow.services.gates import baseline as BL

    _ci_project(repo, 120)  # far more than the evidence sample keeps
    run_cli(repo, "--json", "gate", "run", "T1", "ci", agent="worker")
    ci = _state(repo).items["T1"].gates["ci"]
    # The record links the full list as a blob stub -- readable with the record, not only on
    # the machine whose run wrote the list to a local file (B86370c6924).
    stub = ci.evidence.get("failing_ids")
    assert isinstance(stub, dict) and isinstance(stub.get(B.KEY), str), ci.evidence
    assert len(BL.read_full(repo, stub)) == 120


def test_ci_with_a_failure_not_on_the_base_stays_failed_and_says_so(repo, monkeypatch) -> None:
    from ddflow.api import gate_baseline as GB

    ids = [f"tests/t.py::test_{i}" for i in range(80)] + [
        "tests/test_timing.py::test_lookup_latency_budget"
    ]
    monkeypatch.setattr(GB, "_ci_ids", lambda *a: ids)
    monkeypatch.setattr(GB, "_unit_tests_def", lambda *a: SimpleNamespace(command="pytest"))
    monkeypatch.setattr(BR, "on_base", lambda *a: {"red": False, "still_failing": ids[:-1]})
    cfg = SimpleNamespace(gates=SimpleNamespace(baseline_cache_ttl_s=0, baseline_red_auto="on"))
    ev: dict = {"ci_scope": {"sha": "abc"}}
    it = SimpleNamespace(id="T1", base="main")
    gdef = SimpleNamespace(command="ddflow ci run")
    outcome, why = GB.apply(repo, cfg, "a", it, "ci", gdef, "failed", ev, None, False)
    assert outcome == "failed"
    assert "test_lookup_latency_budget" in why and "timing flake" in why
    assert ev["baseline"]["new_failures"] == [ids[-1]]


def test_a_failed_lint_check_keeps_ci_from_being_baseline_red() -> None:
    from ddflow.services.ci.types import Check

    tests = Check("unit tests + end-to-end scenarios", False)
    assert BL.only_tests_failed([Check("ruff check", True), tests, Check("command", False)])
    assert not BL.only_tests_failed([Check("ruff check", False), tests])


def test_a_malformed_cache_is_dropped_not_fatal_and_a_name_after_equals_matches(repo) -> None:
    from ddflow.api import gate_baseline as GB

    sha = "a" * 40
    path = repo / BL.CACHE_DIR / f"{sha}.{BL.digest('')[:12]}.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"ids": {"a": {}, "b": true}}')
    BL.cache_store(repo, sha, {"c": True}, 3600)
    assert BL.cache_load(repo, sha, 3600) == {"c": True}
    assert GB._names(f"--deselect={ID}", ID)


def test_a_fail_fast_ci_probe_never_attributes_its_failures(repo, monkeypatch) -> None:
    from ddflow.api import gate_baseline as GB

    monkeypatch.setattr(GB, "_ci_ids", lambda *a: [ID])  # one id: of how many that never ran?
    monkeypatch.setattr(GB, "_unit_tests_def", lambda *a: SimpleNamespace(command="pytest -x -q"))

    def boom(*a):
        raise AssertionError("the base must not be asked")

    monkeypatch.setattr(BR, "on_base", boom)
    cfg = SimpleNamespace(gates=SimpleNamespace(baseline_cache_ttl_s=0, baseline_red_auto="on"))
    it = SimpleNamespace(id="T1", base="main")
    got = GB.apply(
        repo,
        cfg,
        "a",
        it,
        "ci",
        SimpleNamespace(command="ddflow ci run"),
        "failed",
        {},
        None,
        False,
    )
    assert got[0] == "failed"


def test_a_ci_whose_always_run_guards_never_ran_is_not_baseline_red(repo, monkeypatch) -> None:
    """B47a1dbeba0: every selected failure is baseline-red, but the always-run guards never ran
    (the selection failed first, so they were skipped). That is NOT baseline-red: a regression
    in a guard is exactly what they exist to catch, and they were never checked."""
    from ddflow.api import gate_baseline as GB

    monkeypatch.setattr(GB, "_ci_ids", lambda *a: [ID])
    monkeypatch.setattr(GB, "_unit_tests_def", lambda *a: SimpleNamespace(command="pytest"))
    monkeypatch.setattr(
        BR,
        "on_base",
        lambda *a: {"red": True, "still_failing": [ID], "base": "main", "base_sha": "a" * 40},
    )
    cfg = SimpleNamespace(gates=SimpleNamespace(baseline_cache_ttl_s=0, baseline_red_auto="on"))
    ev: dict = {
        "ci_scope": {
            "scope": "selected",
            "always": 22,
            "always_tests": ["tests/test_guard_a.py", "tests/test_guard_b.py"],
            "ran": {"hooks": True, "selected": False},
        }
    }
    it = SimpleNamespace(id="T1", base="main")
    gdef = SimpleNamespace(command="ddflow ci run")
    outcome, why = GB.apply(repo, cfg, "a", it, "ci", gdef, "failed", ev, None, False)
    assert outcome == "failed", (outcome, why)
    assert "always-run guards" in why
    assert "22 guard file(s)" in why
    assert "tests/test_guard_a.py" in why
    assert outcome != "partial"


def test_an_always_run_guard_reached_by_import_does_not_block_auto_accept(
    repo, monkeypatch
) -> None:
    """B-baseline-red-reach-counts-whole-tree-guards: an always-run guard (`[ci].always_tests`)
    runs for every item, so a change that reaches it by import reaches it trivially -- the reach
    carries no signal that this change caused the failure. When the guard is red on the base
    with the same message (so `red` holds), it must not block auto-accept."""
    from ddflow.services.testselect import Selected, Selection

    guard = "tests/test_always_guard.py"
    (repo / "tests").mkdir(exist_ok=True)
    (repo / guard).write_text("def test_pins():\n    pass\n")
    sel = Selection(
        base="main",
        changed=["ddflow/api/family_schema.py"],
        tests=[Selected(guard, "imports ddflow.api.family_schema", 2)],
    )
    monkeypatch.setattr(BL.TS, "select", lambda tree, base, **kw: sel)
    cfg = SimpleNamespace(
        gates=SimpleNamespace(baseline_red_auto="on"),
        ci=SimpleNamespace(always_tests=[guard]),
    )
    fid = f"{guard}::test_pins"
    auto = BL.auto_decision(cfg, repo, "main", [fid], {"red": True}, {})
    assert auto["accepted"] is True, auto


def test_a_file_reached_by_import_that_is_not_an_always_guard_still_blocks(
    repo, monkeypatch
) -> None:
    """The always-run guard exclusion must not leak to an ordinary test file reached by import:
    reaching THIS file by import may have caused its failure, so auto-accept still declines."""
    from ddflow.services.testselect import Selected, Selection

    sel = Selection(
        base="main",
        changed=["ddflow/api/family_schema.py"],
        tests=[Selected("tests/test_family_schema.py", "imports ddflow.api.family_schema", 2)],
    )
    monkeypatch.setattr(BL.TS, "select", lambda tree, base, **kw: sel)
    cfg = SimpleNamespace(gates=SimpleNamespace(baseline_red_auto="on"))
    fid = "tests/test_family_schema.py::test_agent_family"
    auto = BL.auto_decision(cfg, repo, "main", [fid], {"red": True}, {})
    assert auto["accepted"] is False, auto
    assert "test_family_schema.py" in auto["why"]
