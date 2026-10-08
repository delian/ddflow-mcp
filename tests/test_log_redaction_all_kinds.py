"""D-unify 6: the committed log redacts free text of EVERY text-bearing event kind.

Bug B5deba76d04: only session prompts and notes went through the `log` profile; a secret,
a LAN address, the machine hostname or a home path in a gate's output tail, a bug summary
or a lesson was committed as typed. The choke point is `EventLog._write`; the text-bearing
fields are declared per kind (`core.redact.LOG_TEXT_FIELDS`), and a kind of the vocabulary
that is in neither that table nor `LOG_NO_TEXT` fails here.
"""

from __future__ import annotations

import json
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
    shard = "\n".join(p.read_text() for p in sorted((tmp_path / ".ddflow" / "events").glob("*.jsonl")))
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
