"""Event evolution (B-uni-compat-events): payload versions, upcasters, a fold isolated per
event, and the ratchet on every kind's fields and types.

`tests/fixtures/event_kinds.json` is the committed snapshot of every event kind: its payload
version and, for each field of its ``data``, the JSON types it has been written with. It was
seeded from the shapes in this project's own log (every released ddflow since 0.1.3 wrote
to it), the 0.1.3 fixture log and the events the test suite writes. The ratchet compares
the snapshot with the one on the branch point: a removed kind, a removed field or a changed
type is refused unless the kind's version went up with an upcaster for each step
(`ddflow.core.upcasters`) and the compatibility contract names the new version. An added
kind, field or type-less field is additive and only has to be recorded.

Regenerate (additively -- nothing is ever dropped) from a project's log:

    python tests/test_event_upcasters.py --write [REPO]
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pytest

from ddflow.core import upcasters as U
from ddflow.core.events import OLDER_MARK, Event
from ddflow.core.model import fold, known_kinds

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "event_kinds.json"
CONTRACT = ROOT / "docs" / "ddflow" / "compatibility.md"
#: Logs written by released ddflow versions that must keep fitting the snapshot.
REAL_LOGS = [ROOT / "tests" / "fixtures" / "upgrade" / "log-0.1.3.jsonl"]
#: Keys the LOG adds to any kind's payload, not part of a kind's own shape: the payload
#: version itself and the older-ddflow mark (D-upgrade-skew-guard).
LOG_KEYS = frozenset({U.VERSION_KEY, OLDER_MARK})


# -- the snapshot -------------------------------------------------------------------------


def registered_steps() -> dict[str, list[int]]:
    """kind -> the versions `upcasters.UPCASTERS` has a step FROM."""
    out: dict[str, list[int]] = {}
    for kind, v in U.UPCASTERS:
        out.setdefault(kind, []).append(v)
    return {k: sorted(vs) for k, vs in out.items()}


def named(contract: str, kind: str, version: int) -> bool:
    """Whether the contract names `kind`'s version `version` -- exactly: `k` v2 is not
    named by an entry for `k` v20."""
    return re.search(rf"`{re.escape(kind)}` v{version}(?!\d)", contract) is not None


def json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return type(value).__name__


def observe(events: Iterable[Event]) -> dict[str, dict[str, set[str]]]:
    """kind -> field -> the JSON types seen, after bringing each event to its current
    shape: what the code reading these events is handed."""
    out: dict[str, dict[str, set[str]]] = {}
    for raw in events:
        ev = U.upcast(raw)
        fields = out.setdefault(ev.kind, {})
        for key, value in ev.data.items():
            if key not in LOG_KEYS:
                fields.setdefault(key, set()).add(json_type(value))
    return out


def load(path: Path = FIXTURE) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_log(paths: Iterable[Path]) -> list[Event]:
    return [
        Event.from_json(line)
        for p in paths
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def merged(snapshot: Mapping[str, Any], seen: Mapping[str, Mapping[str, set[str]]]) -> dict:
    """`snapshot` with what `seen` adds -- new kinds, new fields, new types -- and every
    kind's current version. Additive only: nothing in `snapshot` is dropped."""
    kinds = {k: {"v": v["v"], "fields": dict(v["fields"])} for k, v in snapshot["kinds"].items()}
    for kind in sorted(known_kinds() | set(seen)):
        entry = kinds.setdefault(kind, {"v": U.current_version(kind), "fields": {}})
        entry["v"] = U.current_version(kind)
        for field, types in seen.get(kind, {}).items():
            entry["fields"][field] = sorted(set(entry["fields"].get(field, [])) | types)
        entry["fields"] = dict(sorted(entry["fields"].items()))
    return {"format": 1, "kinds": dict(sorted(kinds.items()))}


def drift(
    old: Mapping[str, Any],
    new: Mapping[str, Any],
    *,
    steps: Mapping[str, list[int]] | None = None,
    contract: str | None = None,
) -> list[str]:
    """What changed from `old` to `new` that a reader of the old shape would break on,
    without the version bump, upcasters and contract entry that make it a declared change.
    Empty when every change is additive."""
    steps = registered_steps() if steps is None else steps
    contract = CONTRACT.read_text(encoding="utf-8") if contract is None else contract
    out: list[str] = []
    for kind, was in sorted(old["kinds"].items()):
        now = new["kinds"].get(kind)
        if now is None:
            if was["v"] not in steps.get(kind, []):
                out.append(f"{kind}: kind removed without an upcaster from v{was['v']}")
            if not named(contract, kind, was["v"] + 1):
                out.append(f"{kind}: removal (v{was['v'] + 1}) is not named in {CONTRACT.name}")
            continue
        if now["v"] < was["v"]:
            out.append(f"{kind}: payload version went down, v{was['v']} -> v{now['v']}")
        elif now["v"] > was["v"]:
            missing = [v for v in range(was["v"], now["v"]) if v not in steps.get(kind, [])]
            if missing:
                out.append(f"{kind}: v{now['v']} has no upcaster from v{missing[0]}")
            if not named(contract, kind, now["v"]):
                out.append(f"{kind}: v{now['v']} is not named in {CONTRACT.name}")
            continue  # a declared bump may reshape the payload freely
        for field, types in sorted(was["fields"].items()):
            if field not in now["fields"]:
                out.append(f"{kind}.{field}: field removed without a version bump")
            elif sorted(now["fields"][field]) != sorted(types):
                out.append(
                    f"{kind}.{field}: type changed {sorted(types)} -> "
                    f"{sorted(now['fields'][field])} without a version bump"
                )
    return out


def base_snapshot() -> dict[str, Any] | None:
    """The snapshot as committed at this branch's fork point from main, or None when git or
    main is not available (an sdist, a shallow clone)."""

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True
        ).stdout

    try:
        base = git("merge-base", "HEAD", "main").strip()
        return json.loads(git("show", f"{base}:{FIXTURE.relative_to(ROOT).as_posix()}"))
    except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError):
        return None


# -- upcasting ----------------------------------------------------------------------------


def _ev(kind: str, data: dict, **kw) -> Event:
    return Event(kind=kind, subject=kw.pop("subject", "T1"), data=data, id="e1", **kw)


def test_a_version_1_event_is_returned_untouched() -> None:
    ev = _ev("task.added", {"title": "t"})
    assert U.upcast(ev) is ev


def test_upcasters_chain_to_the_current_version_and_keep_the_id() -> None:
    def rename_title(kind, d):
        return kind, {**{k: v for k, v in d.items() if k != "name"}, "title": d["name"]}

    def int_priority(kind, d):
        return kind, {**d, "priority": int(d["priority"])}

    versions = {"x.added": 3}
    steps = {("x.added", 1): rename_title, ("x.added", 2): int_priority}
    ev = _ev("x.added", {"name": "a", "priority": "7"})
    got = U.upcast(ev, versions=versions, upcasters=steps)
    assert got.data == {"title": "a", "priority": 7, "v": 3}
    assert (got.id, got.lamport, got.subject) == (ev.id, ev.lamport, ev.subject)
    assert ev.data == {"name": "a", "priority": "7"}, "the upcaster must not mutate the log"
    # An event already at version 2 runs only the last step.
    mid = _ev("x.added", {"title": "a", "priority": "7", "v": 2})
    assert U.upcast(mid, versions=versions, upcasters=steps).data["priority"] == 7


def test_an_upcaster_may_rename_the_kind() -> None:
    steps = {("old.kind", 1): lambda k, d: ("new.kind", dict(d))}
    got = U.upcast(_ev("old.kind", {"a": 1}), versions={"old.kind": 2}, upcasters=steps)
    assert (got.kind, got.data) == ("new.kind", {"a": 1})


def test_a_newer_payload_is_refused_not_guessed() -> None:
    with pytest.raises(U.NewerPayload):
        U.upcast(_ev("task.added", {"title": "t", "v": 2}))


@pytest.mark.parametrize("bad", ["2", 0, -1, True, 1.5, None])
def test_a_malformed_version_is_an_error(bad) -> None:
    with pytest.raises(ValueError):
        U.upcast(_ev("task.added", {"v": bad}))


def test_a_missing_step_is_an_error() -> None:
    with pytest.raises(ValueError, match="no upcaster"):
        U.upcast(_ev("x.added", {}), versions={"x.added": 2}, upcasters={})


def test_stamp_leaves_a_version_1_payload_byte_identical(monkeypatch) -> None:
    assert U.stamp("task.added", {"title": "t"}) == {"title": "t"}
    monkeypatch.setitem(U.PAYLOAD_VERSIONS, "task.added", 2)
    assert U.stamp("task.added", {"title": "t"}) == {"title": "t", "v": 2}


def test_a_line_whose_data_is_not_an_object_is_an_unreadable_line(tmp_path) -> None:
    """Every reader of a payload takes it as a mapping (the fold, doctor's orphan count, the
    stamp guard): such a line is reported as unreadable instead of reaching them (roborev)."""
    from ddflow.infra.log import EventLog, _parse_event

    bad = '{"agent":"a","data":[1,2],"id":"ex","kind":"phase.added","lamport":1,"subject":"P0"}'
    assert _parse_event(bad) is None
    for not_an_object in ("[1, 2]", '"x"', "42", "null"):  # critic #3: still one bucket
        assert _parse_event(not_an_object) is None
    log = EventLog(tmp_path, agent_id="a1")
    log.append("phase.added", "P1", {"title": "t"})
    with log.shard.open("a", encoding="utf-8") as f:
        f.write(bad + "\n")
    assert [e.subject for e in log.read_all() if e.kind == "phase.added"] == ["P1"]


def test_the_log_does_not_mutate_the_callers_payload(tmp_path, monkeypatch) -> None:
    from ddflow.infra.log import EventLog

    monkeypatch.setitem(U.PAYLOAD_VERSIONS, "phase.added", 2)
    monkeypatch.setitem(U.UPCASTERS, ("phase.added", 1), lambda k, d: (k, dict(d)))
    data = {"title": "t"}
    EventLog(tmp_path, agent_id="a1").append("phase.added", "P1", data)
    assert data == {"title": "t"}


def test_the_log_writes_a_kind_at_its_current_version(tmp_path, monkeypatch) -> None:
    from ddflow.infra.log import EventLog

    log = EventLog(tmp_path, agent_id="a1")
    assert "v" not in log.append("phase.added", "P1", {"title": "t"}).data
    monkeypatch.setitem(U.PAYLOAD_VERSIONS, "phase.added", 2)
    monkeypatch.setitem(U.UPCASTERS, ("phase.added", 1), lambda k, d: (k, dict(d)))
    assert log.append("phase.added", "P2", {"title": "t"}).data["v"] == 2
    st = fold(log.read_all())
    assert {"P1", "P2"} <= set(st.items)


def test_the_logs_own_writes_carry_their_payload_version_too(tmp_path, monkeypatch) -> None:
    """`ddflow.seen` is written by the log itself, past `append` (roborev on d3761dba)."""
    from ddflow.core.events import SEEN_KIND
    from ddflow.infra.log import EventLog

    monkeypatch.setitem(U.PAYLOAD_VERSIONS, SEEN_KIND, 2)
    monkeypatch.setitem(U.UPCASTERS, (SEEN_KIND, 1), lambda k, d: (k, dict(d)))
    log = EventLog(tmp_path, agent_id="a1")
    log.append("phase.added", "P1", {"title": "t"})
    seen = [e for e in log.read_all() if e.kind == SEEN_KIND]
    assert seen and all(e.data["v"] == 2 for e in seen)


def test_the_fold_hands_handlers_the_upcast_shape(monkeypatch) -> None:
    monkeypatch.setitem(U.PAYLOAD_VERSIONS, "phase.added", 2)
    monkeypatch.setitem(
        U.UPCASTERS, ("phase.added", 1), lambda k, d: (k, {**d, "title": d["old_title"]})
    )
    st = fold([_ev("phase.added", {"old_title": "renamed"}, subject="P1")])
    assert st.items["P1"].title == "renamed"


def test_the_older_ddflow_mark_is_read_from_the_event_as_written(monkeypatch) -> None:
    """An upcaster rebuilds the payload from the fields of the kind's shape; the log's own
    older-version mark is not one of them and must still be counted (rubber_duck #1)."""
    monkeypatch.setitem(U.PAYLOAD_VERSIONS, "phase.added", 2)
    monkeypatch.setitem(U.UPCASTERS, ("phase.added", 1), lambda k, d: (k, {"title": d["title"]}))
    st = fold([_ev("phase.added", {"title": "t", OLDER_MARK: "0.1.3"}, subject="P1")])
    assert st.older_version_events == {"0.1.3": 1}
    assert st.items["P1"].title == "t"


# -- the fold, isolated per event ---------------------------------------------------------


def _malformed_then_good() -> list[Event]:
    return [
        Event(kind="phase.added", subject="P0", data={"title": "before"}, lamport=1, id="e0"),
        # `evidence` must be a mapping; an int makes the gate handler raise.
        Event(
            kind="gate.passed",
            subject="P0",
            data={"gate": "ci", "evidence": 5},
            lamport=2,
            agent="a1",
            id="ebad",
        ),
        Event(kind="phase.added", subject="P1", data={"title": "after"}, lamport=3, id="e2"),
    ]


def test_one_malformed_event_no_longer_aborts_the_fold() -> None:
    """Regression: before B-uni-compat-events a single malformed event raised out of even a
    non-strict fold, and every event after it was lost to every reader."""
    st = fold(_malformed_then_good(), strict=False)
    assert {"P0", "P1"} <= set(st.items)
    [p] = st.fold_problems
    assert (p.event, p.kind, p.lamport, p.agent) == ("ebad", "gate.passed", 2, "a1")
    assert p.error.startswith("TypeError")


@pytest.mark.parametrize("payload", [[], None, "x", 5])
def test_a_payload_that_is_not_a_mapping_is_a_fold_problem(payload) -> None:
    evs = [
        _ev("phase.added", payload, subject="P0"),
        _ev("phase.added", {"title": "t"}, subject="P1"),
    ]
    st = fold(evs, strict=False)
    assert [p.error.split(":")[0] for p in st.fold_problems] == ["AttributeError"]
    assert "P1" in st.items


def test_a_strict_fold_still_raises_on_a_malformed_event() -> None:
    with pytest.raises(TypeError):
        fold(_malformed_then_good(), strict=True)


def test_a_newer_payload_is_counted_and_skipped_like_an_unknown_kind() -> None:
    evs = [_ev("phase.added", {"title": "x", "v": 9}, subject="P9")]
    st = fold(evs, strict=False)
    assert "P9" not in st.items
    assert st.skipped_kinds == {"phase.added (payload v9)": 1}
    assert st.fold_problems == []
    with pytest.raises(U.NewerPayload):
        fold(evs, strict=True)


def test_a_malformed_version_is_a_fold_problem() -> None:
    st = fold([_ev("phase.added", {"title": "x", "v": "two"}, subject="P9")], strict=False)
    assert [p.kind for p in st.fold_problems] == ["phase.added"]
    assert "P9" not in st.items


def test_doctor_names_each_fold_problem() -> None:
    from ddflow.api.reporting.health import FOLD_PROBLEMS_SHOWN, fold_problem_notes

    st = fold(_malformed_then_good(), strict=False)
    [note] = fold_problem_notes(st)
    assert "1 event(s) could not be folded" in note
    assert "ebad gate.passed at lamport 2 by a1: TypeError" in note
    many = _malformed_then_good()[1:2] * (FOLD_PROBLEMS_SHOWN + 2)
    [note] = fold_problem_notes(fold(many, strict=False))
    assert "... and 2 more" in note
    assert fold_problem_notes(fold([], strict=False)) == []


# -- the snapshot and its ratchet ---------------------------------------------------------


def test_the_snapshot_records_every_kind_at_its_current_version() -> None:
    snap = load()["kinds"]
    missing = sorted(known_kinds() - set(snap))
    assert not missing, (
        f"new event kind(s) {missing}: record them (additive) with "
        "`python tests/test_event_upcasters.py --write`"
    )
    steps = registered_steps()
    contract = CONTRACT.read_text(encoding="utf-8")
    gone = sorted(
        k
        for k in set(snap) - known_kinds()
        if snap[k]["v"] not in steps.get(k, []) or not named(contract, k, snap[k]["v"] + 1)
    )
    assert not gone, (
        f"kind(s) {gone} removed without an upcaster from their last version and an entry "
        f"in {CONTRACT.name}"
    )
    wrong = {k: (e["v"], U.current_version(k)) for k, e in snap.items() if k in known_kinds()}
    assert {k: v for k, v in wrong.items() if v[0] != v[1]} == {}


def test_every_version_bump_has_its_upcasters_and_a_contract_entry() -> None:
    contract = CONTRACT.read_text(encoding="utf-8")
    steps = registered_steps()
    for kind, version in U.PAYLOAD_VERSIONS.items():
        assert [v for v in range(1, version) if v not in steps.get(kind, [])] == []
        assert named(contract, kind, version)


def test_the_released_logs_fit_the_snapshot() -> None:
    """Every field a released ddflow wrote is recorded with every type it was written as;
    a type the snapshot does not list is a retype that needs a version bump."""
    snap = load()
    seen = observe(read_log(REAL_LOGS))
    assert merged(snap, seen) == snap


def test_the_released_logs_still_fold_strictly() -> None:
    st = fold(sorted(read_log(REAL_LOGS), key=Event.sort_key), strict=True)
    assert st.fold_problems == [] and st.skipped_kinds == {}
    assert st.items


def test_the_snapshot_only_grows_without_a_declared_bump() -> None:
    base = base_snapshot()
    if base is None:
        pytest.skip("no git history with main to compare the snapshot against")
    assert drift(base, load()) == []


def test_drift_refuses_a_removed_or_retyped_field_and_accepts_a_declared_bump() -> None:
    old = {"format": 1, "kinds": {"k.a": {"v": 1, "fields": {"x": ["str"], "y": ["int"]}}}}

    def new(v=1, **fields):
        return {"format": 1, "kinds": {"k.a": {"v": v, "fields": fields}}}

    assert drift(old, new(x=["str"], y=["int"], z=["list"]), steps={}, contract="") == []
    assert drift(old, new(x=["str"]), steps={}, contract="") == [
        "k.a.y: field removed without a version bump"
    ]
    assert drift(old, new(x=["int"], y=["int"]), steps={}, contract="") == [
        "k.a.x: type changed ['str'] -> ['int'] without a version bump"
    ]
    gone = {"format": 1, "kinds": {}}
    assert drift(old, gone, steps={}, contract="") == [
        "k.a: kind removed without an upcaster from v1",
        f"k.a: removal (v2) is not named in {CONTRACT.name}",
    ]
    assert drift(old, gone, steps={"k.a": [1]}, contract="") == [
        f"k.a: removal (v2) is not named in {CONTRACT.name}"
    ]
    assert drift(old, gone, steps={"k.a": [1]}, contract="`k.a` v2 becomes k.b") == []
    assert drift(old, gone, steps={"k.a": [1]}, contract="`k.a` v20 becomes k.b") == [
        f"k.a: removal (v2) is not named in {CONTRACT.name}"
    ]
    bumped = new(2, x=["int"])
    assert drift(old, bumped, steps={}, contract="") == [
        "k.a: v2 has no upcaster from v1",
        f"k.a: v2 is not named in {CONTRACT.name}",
    ]
    assert drift(old, bumped, steps={"k.a": [1]}, contract="`k.a` v2: x is an int") == []
    assert drift(new(2, x=["int"]), new(1, x=["int"]), steps={}, contract="") == [
        "k.a: payload version went down, v2 -> v1"
    ]


def test_the_snapshot_is_committed_in_its_canonical_form() -> None:
    assert FIXTURE.read_text(encoding="utf-8") == dump(load())


def test_merge_is_additive() -> None:
    snap = {"format": 1, "kinds": {"task.added": {"v": 1, "fields": {"title": ["str"]}}}}
    got = merged(snap, {"task.added": {"body": {"str"}, "title": {"null"}}})
    assert got["kinds"]["task.added"]["fields"] == {"body": ["str"], "title": ["null", "str"]}
    assert known_kinds() <= set(got["kinds"])


def dump(snapshot: Mapping[str, Any]) -> str:
    """The snapshot as committed: one field per line, its types inline, so a diff of the
    file reads as the change to the shapes."""
    text = json.dumps(snapshot, indent=1, sort_keys=True)
    return (
        re.sub(
            r"\[\s*([^\[\]{}]*?)\s*\]",
            lambda m: "[" + ", ".join(x.strip() for x in m.group(1).split(",") if x.strip()) + "]",
            text,
        )
        + "\n"
    )


def _write(repo: Path) -> None:
    logs = sorted((repo / ".ddflow" / "events").glob("*.jsonl")) + REAL_LOGS
    snap = load() if FIXTURE.exists() else {"format": 1, "kinds": {}}
    out = merged(snap, observe(read_log(logs)))
    FIXTURE.write_text(dump(out), encoding="utf-8")
    print(f"wrote {FIXTURE.relative_to(ROOT)}: {len(out['kinds'])} kinds")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] != "--write":
        sys.exit(__doc__)
    _write(Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT)
