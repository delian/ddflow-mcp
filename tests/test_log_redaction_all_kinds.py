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
    declared = set(R.LOG_TEXT_FIELDS) | set(R.LOG_NO_TEXT) | set(R.LOG_ALL_TEXT)
    classes = [set(R.LOG_TEXT_FIELDS), set(R.LOG_NO_TEXT), set(R.LOG_ALL_TEXT)]
    assert sum(len(c) for c in classes) == len(declared), "a kind is in two classes"
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
    r"(^|_)(id|ids|sha|digest|hash|path|paths|url|branch|worktree|head|base|tree|globs|tags"
    r"|needs|parent|related|supersedes|extends|fixes|duplicate_of|fix_task|resources|number"
    r"|subject)$"
)
#: str/dict/list fields of undeclared-text kinds that are NOT lookup-named, each with why.
_NOT_TEXT = {
    ("approval.granted", "user"): "a user id",
    ("backmerge.recorded", "into"): "a branch name",
    ("bug.found", "key"): "a dedupe key",
    ("bug.found", "scope"): "a scope word",
    ("bug.found", "severity"): "a severity word",
    ("ci.result", "status"): "a status word",
    ("ddflow.seen", "version"): "a version",
    ("decision.recorded", "item"): "an item id",
    ("decision.recorded", "status"): "a status word",
    ("def.merged", "source"): "an origin id or path",
    ("def.recorded", "source"): "an origin id or path",
    ("def.retired", "source"): "an origin id or path",
    ("def.superseded", "source"): "an origin id or path",
    ("def.updated", "source"): "an origin id or path",
    ("export.disabled", "document"): "a document name",
    ("export.enabled", "document"): "a document name",
    ("export.enabled", "mode"): "a mode word",
    ("flow.chosen", "knob"): "a config key",
    ("flow.chosen", "user"): "a user id",
    ("gate.failed", "gate"): "a gate name",
    ("gate.failed", "kind"): "a kind word",
    ("gate.out_of_order", "gate"): "a gate name",
    ("gate.out_of_order", "policy"): "a policy word",
    ("gate.partial", "gate"): "a gate name",
    ("gate.passed", "gate"): "a gate name",
    ("gate.passed", "kind"): "a kind word",
    ("gate.skipped", "gate"): "a gate name",
    ("gate.unavailable", "gate"): "a gate name",
    ("job.started", "host"): "a machine id compared for liveness",
    ("job.started", "item"): "an item id",
    ("job.started", "log"): "a path that is opened",
    ("job.started", "proc_start"): "a process start stamp",
    ("lease.acquired", "holder"): "an agent id",
    ("lease.acquired", "kind"): "a kind word",
    ("lease.expired", "holder"): "an agent id",
    ("lease.released", "by"): "an agent id",
    ("lease.released", "event"): "an event id",
    ("lease.released", "holder"): "an agent id",
    ("link.recorded", "relation"): "a relation word",
    ("link.recorded", "target"): "an id",
    ("phase.added", "source"): "an origin id or path",
    ("pr.synced", "kind"): "a kind word",
    ("pr.synced", "queue_state"): "a state word",
    ("pr.synced", "state"): "a state word",
    ("research.recorded", "verdict"): "a verdict word",
    ("review.triaged", "severity"): "a severity word",
    ("review.triaged", "verdict"): "a verdict word",
    ("reviewer.configured", "user"): "a user id",
    ("session.note", "item"): "an item id",
    ("session.started", "tool"): "a harness name",
    ("skew.overridden", "session"): "a session id",
    ("task.added", "port_from"): "an id",
    ("task.added", "port_of"): "an id",
    ("task.added", "port_strategy"): "a strategy word",
    ("task.added", "source"): "an origin id or path",
    ("trigger.fired", "items"): "item ids",
    ("trigger.fired", "job"): "a job id",
    ("trigger.fired", "key"): "a dedupe key",
    ("trigger.suppressed", "key"): "a dedupe key",
    ("approval.granted", "host"): "a machine id compared by liveness",
    ("backmerge.recorded", "forge"): "a forge name",
    ("bug.found", "item"): "an item id",
    ("ci.result", "stage"): "a stage word",
    ("ddflow.seen", "install"): "an install kind",
    ("decision.recorded", "decided_by"): "an agent id",
    ("decision.superseded", "by"): "an agent id",
    ("def.merged", "kind"): "a kind word",
    ("def.recorded", "kind"): "a kind word",
    ("def.retired", "kind"): "a kind word",
    ("def.superseded", "kind"): "a kind word",
    ("def.updated", "kind"): "a kind word",
    ("deploy.recorded", "env"): "an environment name",
    ("export.disabled", "by"): "an agent id",
    ("export.enabled", "by"): "an agent id",
    ("flow.chosen", "by"): "an agent id",
    ("gate.failed", "by"): "an agent id",
    ("gate.out_of_order", "ahead"): "gate names",
    ("gate.partial", "by"): "an agent id",
    ("gate.passed", "by"): "an agent id",
    ("gate.skipped", "by"): "an agent id",
    ("gate.started", "gate"): "a gate name",
    ("gate.unavailable", "by"): "an agent id",
    ("item.abandoned", "kind"): "a kind word",
    ("item.blocked", "kind"): "a kind word",
    ("item.completed", "kind"): "a kind word",
    ("item.resolved", "kind"): "a kind word",
    ("item.unblocked", "kind"): "a kind word",
    ("job.started", "cwd"): "a directory that is opened",
    ("lease.acquired", "agent"): "an agent id",
    ("lease.expired", "event"): "an event id",
    ("lease.released", "agent"): "an agent id",
    ("lease.renewed", "holder"): "an agent id",
    ("lesson.recorded", "seen_in"): "item ids",
    ("link.recorded", "by"): "an agent id",
    ("memory.recorded", "origin_at"): "a timestamp",
    ("phase.added", "kind"): "a kind word",
    ("pr.synced", "forge"): "a forge name",
    ("record.extended", "relation"): "a relation word",
    ("research.recorded", "item"): "an item id",
    ("review.triaged", "gate"): "a gate name",
    ("reviewer.configured", "kind"): "a kind word",
    ("session.note", "at"): "a timestamp",
    ("session.prompt", "item"): "an item id",
    ("session.started", "model"): "a model id",
    ("skew.overridden", "log_version"): "a version",
    ("task.added", "kind"): "a kind word",
    ("trigger.evaluated", "triggers"): "trigger ids",
    ("trigger.fired", "events"): "event ids",
    ("trigger.suppressed", "events"): "event ids",
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
    missing = [
        name
        for name, types in KINDS[kind]["fields"].items()
        if name not in declared
        and set(types) & {"str", "dict", "list"}
        and not _LOOKUP.search(name)
        and (kind, name) not in _NOT_TEXT
        and name != "dedupe"
    ]
    assert not missing, f"{kind}: {missing} neither declared text nor a justified lookup field"


def test_an_undeclared_kind_is_redacted_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unknown is not text-free: a kind in neither table redacts every field."""
    monkeypatch.setenv("HOME", "/home/zedd")
    red = R.Redactor("log", secret_patterns=[r"sk-[A-Za-z0-9]{16,}"], home="/home/zedd")
    out = R.redact_event_data("brand.new", {"a": PROBE, "n": 3, "d": {"tail": PROBE}}, red)
    assert SECRET not in json.dumps(out) and out["n"] == 3


def test_a_leaf_is_redacted_whatever_its_key() -> None:
    """The keys of a text field's dict come from data: `{"path": <home path>}` is text."""
    red = R.Redactor("log", secret_patterns=[r"sk-[A-Za-z0-9]{16,}"], home="/home/zedd")
    out = R.redact_event_data(
        "gate.passed",
        {
            "evidence": {
                "tests": PROBE,
                "files": [PROBE],
                "path": HOME,
                "output_log": "/home/zedd/x",
            }
        },
        red,
    )
    flat = json.dumps(out)
    assert SECRET not in flat and "/home/zedd" not in flat


def test_a_relative_output_file_survives_redaction() -> None:
    """What gate evidence really holds: a repo-relative file name with dots and hyphens."""
    red = R.Redactor("log", secret_patterns=[], hostname="box", home="/home/zedd")
    name = ".ddflow/local/reviews/B-x.critic.lan-deepseek-v4.1-flash.20261007T121514-76265fac.jsonl"
    log = ".ddflow/runs/B-x/ci-20261007T124010443798Z-2485073.log"
    out = R.redact_event_data(
        "gate.passed", {"evidence": {"output_file": name, "output_log": log}}, red
    )
    assert out["evidence"] == {"output_file": name, "output_log": log}


@pytest.mark.parametrize("kind", sorted(R.LOG_ALL_TEXT))
def test_an_opaque_kind_is_redacted_whole(kind: str, tmp_path: Path, monkeypatch) -> None:
    """`external.observed` titles and `port.applied` git errors are free text the fixture
    does not describe (roborev on the first commit)."""
    monkeypatch.setenv("HOME", "/home/zedd")
    log = EventLog(tmp_path, "T")
    log.stamp = False
    log.append(kind, "s", {"title": PROBE, "reason": PROBE})
    shard = "".join(p.read_text() for p in (tmp_path / ".ddflow" / "events").glob("*.jsonl"))
    assert SECRET not in shard and "/home/zedd" not in shard
