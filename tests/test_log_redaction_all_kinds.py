"""D-unify 6: the committed log redacts free text of EVERY text-bearing event kind.

Bug B5deba76d04: only session prompts and notes went through the `log` profile; a secret,
a LAN address, the machine hostname or a home path in a gate's output tail, a bug summary
or a lesson was committed as typed. The choke point is `EventLog._write`; the text-bearing
fields are declared per kind (`core.redact.LOG_TEXT_FIELDS`), and a kind of the vocabulary
that is in neither that table nor `LOG_NO_TEXT` fails here.
"""

from __future__ import annotations

import json
import re
import socket
from pathlib import Path

import pytest

from ddflow.core import redact as R
from ddflow.infra.log import EventLog

FIXTURE = Path(__file__).parent / "fixtures" / "event_kinds.json"
KINDS = json.loads(FIXTURE.read_text())["kinds"]

SECRET = "sk-abcdefghijklmnop1234567890"
LAN = ".".join(("10", "77", "3", "91"))  # built, so no private host is committed text
HOST = "u3-testbox-77"
HOME = "/home/zedd/work/proj/secret_file.py"
PROBE = f"see {SECRET} at {LAN} on {HOST} in {HOME} password=hunter2hunter2"
LEAKS = (SECRET, LAN, HOST, "/home/zedd", "hunter2hunter2")
SHA = "0123456789abcdef0123456789abcdef01234567"

TEXT_KINDS = sorted(k for k in KINDS if k in R.LOG_TEXT_FIELDS)


def test_every_kind_is_declared() -> None:
    declared = set(R.LOG_TEXT_FIELDS) | set(R.LOG_NO_TEXT)
    assert not (set(R.LOG_TEXT_FIELDS) & set(R.LOG_NO_TEXT))
    assert set(KINDS) <= declared, f"undeclared kinds: {sorted(set(KINDS) - declared)}"
    assert declared <= set(KINDS), f"declared but unknown: {sorted(declared - set(KINDS))}"


def test_declared_fields_exist_in_the_kind() -> None:
    for kind, names in R.LOG_TEXT_FIELDS.items():
        missing = set(names) - set(KINDS[kind]["fields"])
        assert not missing, f"{kind}: {sorted(missing)} not fields of the kind"


def _value(types: list[str]):
    if "str" in types:
        return PROBE
    if "dict" in types:
        return {"note": PROBE, "tail": PROBE, "sha": SHA, "digest": SHA, "tree_sha": SHA}
    if "list" in types:
        return [PROBE, {"detail": PROBE}]
    raise AssertionError(types)


@pytest.mark.parametrize("kind", TEXT_KINDS)
def test_no_text_field_leaks(kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "gethostname", lambda: HOST)
    monkeypatch.setenv("HOME", "/home/zedd")
    log = EventLog(tmp_path, "T")
    log.stamp = False
    fields = KINDS[kind]["fields"]
    data = {name: _value(fields[name]) for name in R.LOG_TEXT_FIELDS[kind]}
    if "sha" in fields:
        data["sha"] = SHA
    log.append(kind, "subject-1", data)
    shard = "\n".join(
        p.read_text() for p in sorted((tmp_path / ".ddflow" / "events").glob("*.jsonl"))
    )
    for leak in LEAKS:
        assert leak not in shard, f"{kind}: {leak!r} was committed"
    assert "[REDACTED" in shard, f"{kind}: nothing was redacted"
    for name, val in data.items():
        if isinstance(val, dict) and "sha" in val:
            assert SHA in shard, f"{kind}.{name}: a sha/digest was altered"
    if "sha" in data:
        assert SHA in shard


def test_ids_and_paths_stay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A lookup key is not text: the worktree path of a lease and a sha are unchanged."""
    monkeypatch.setenv("HOME", "/home/zedd")
    log = EventLog(tmp_path, "T")
    log.stamp = False
    wt = "/home/zedd/work/proj/.ddflow/worktrees/x"
    log.append("lease.acquired", "x", {"holder": "T", "worktree": wt, "branch": "b"})
    shard = "".join(p.read_text() for p in (tmp_path / ".ddflow" / "events").glob("*.jsonl"))
    assert wt in shard
    assert SECRET not in shard and LAN not in shard


#: A field of a kind that is neither declared text nor named like a lookup key must be
#: justified here: the reviewer's point was that the table is only as good as its author's
#: reading of the vocabulary, so every str/dict/list field has to be accounted for.
_LOOKUP = re.compile(
    r"(^|_)(id|ids|item|key|kind|by|user|host|agent|holder|gate|sha|digest|hash|path|paths|url"
    r"|branch|worktree|base|into|head|tree|target|relation|subject|event|log|forge|number|state"
    r"|status|stage|env|scope|severity|verdict|policy|mode|document|knob|value|tool|model|version"
    r"|install|session|source|cwd|globs|tags|needs|parent|related|supersedes|extends|fixes"
    r"|duplicate_of|fix_task|resources|sites|seen_in|sources|events|items|job|triggers"
    r"|ahead|at|port_\w+|origin_at|proc_start)$"
)
#: str/dict/list fields of undeclared-text kinds that are NOT lookup-named, each with why.
_NOT_TEXT = {
    ("bug.fixed", "regression_verified"): "an outcome word",
    ("bug.reopened", "was"): "a state word",
    ("item.unblocked", "was"): "a state word",
    ("gate.partial", "outcome"): "an outcome word",
    ("item.reopened", "claims"): "lease claims: ids and paths",
    ("lease.acquired", "note"): "ddflow's own note naming agent ids (a lookup)",
    ("pr.synced", "checks"): "a status word",
    ("record.extended", "who"): "an agent id",
    ("research.recorded", "budget"): "a tier word",
    ("review.triaged", "location"): "file:line, a lookup",
    ("schedule.defined", "cadence"): "numbers and cron words",
    ("skew.overridden", "running"): "a version",
    ("worktree.merged", "landed_after"): "a sha",
    ("bug.fixed", "regression_test"): "a test id",
    ("def.superseded", "successor"): "an id",
    ("def.merged", "successor"): "an id",
    ("item.completed", "ledger"): "files, tests and counts",
    ("item.resolved", "claim"): "a lease claim: ids and paths",
    ("schedule.defined", "concurrency_group"): "a group id",
    ("pr.synced", "queue"): "a queue state word",
    ("worktree.merged", "landed_before"): "a sha",
    ("trigger.evaluated", "now"): "a timestamp",
    ("bug.fixed", "regression_tests"): "test ids",
    ("item.completed", "overridden"): "gate names",
    ("item.resolved", "keep"): "an id",
    ("pr.synced", "review"): "a review state word",
    ("def.merged", "provenance"): "ids of origin",
    ("def.recorded", "provenance"): "ids of origin",
    ("def.retired", "provenance"): "ids of origin",
    ("def.superseded", "provenance"): "ids of origin",
    ("def.updated", "provenance"): "ids of origin",
}


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_every_text_looking_field_is_accounted_for(kind: str) -> None:
    declared = set(R.LOG_TEXT_FIELDS.get(kind, ()))
    for name, types in KINDS[kind]["fields"].items():
        if name in declared or not set(types) & {"str", "dict", "list"}:
            continue
        ok = _LOOKUP.search(name) or (kind, name) in _NOT_TEXT or name == "dedupe"
        assert ok, f"{kind}.{name} ({types}) is neither declared text nor a lookup field"


def test_an_undeclared_kind_is_redacted_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unknown is not text-free: a kind in neither table redacts every field."""
    monkeypatch.setenv("HOME", "/home/zedd")
    red = R.Redactor("log", secret_patterns=[r"sk-[A-Za-z0-9]{16,}"], home="/home/zedd")
    out = R.redact_event_data("brand.new", {"a": PROBE, "n": 3, "d": {"tail": PROBE}}, red)
    assert SECRET not in json.dumps(out) and out["n"] == 3


def test_a_leaf_under_a_lookup_looking_key_is_still_redacted() -> None:
    """The keys of a text field's dict come from data: `{"tests": <tail>}` is text."""
    red = R.Redactor("log", secret_patterns=[r"sk-[A-Za-z0-9]{16,}"], home="/home/zedd")
    out = R.redact_event_data(
        "gate.passed",
        {"evidence": {"tests": PROBE, "files": [PROBE], "worktree": "/home/zedd/w"}},
        red,
    )
    flat = json.dumps(out)
    assert SECRET not in flat and "/home/zedd/work" not in flat
    assert out["evidence"]["worktree"] == "/home/zedd/w"  # a path that is opened stays
