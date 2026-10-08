"""D-unify 6: the committed log redacts free text of EVERY text-bearing event kind.

Bug B5deba76d04: only session prompts and notes went through the `log` profile; a secret,
a LAN address, the machine hostname or a home path in a gate's output tail, a bug summary
or a lesson was committed as typed. The choke point is `EventLog._write`; the text-bearing
fields are declared per kind (`core.redact.LOG_TEXT_FIELDS`), and a kind of the vocabulary
whose fields are neither declared text nor a justified lookup fails here.
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


def test_declared_kinds_are_known() -> None:
    assert set(R.LOG_TEXT_FIELDS) <= set(KINDS), sorted(set(R.LOG_TEXT_FIELDS) - set(KINDS))
    assert {k for k, _ in R.LOG_VERBATIM_FIELDS} <= set(KINDS)


def test_a_verbatim_pair_is_a_field_of_its_kind() -> None:
    for kind, name in R.LOG_VERBATIM_FIELDS:
        assert name in KINDS[kind]["fields"], f"{kind}.{name} is not a field of the kind"


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


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_every_text_looking_field_is_accounted_for(kind: str) -> None:
    declared = set(R.LOG_TEXT_FIELDS.get(kind, ()))
    missing = [
        name
        for name, types in KINDS[kind]["fields"].items()
        if name not in declared
        and set(types) & {"str", "dict", "list"}
        and name not in R.LOG_VERBATIM_NAMES
        and name not in R.LOG_TEXT_NAMES
        and (kind, name) not in R.LOG_VERBATIM_FIELDS
    ]
    assert not missing, (
        f"{kind}: {missing} neither declared text nor a justified lookup field (R.LOG_VERBATIM_*)"
    )


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


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_a_field_the_vocabulary_does_not_know_is_redacted(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unknown is not text-free: a claim's `--note`, a link's `--reason`, a PR's feedback and a
    sibling repository's item title are not in the vocabulary, and are not committed as typed
    (roborev on the first fix)."""
    monkeypatch.setenv("HOME", "/home/zedd")
    log = EventLog(tmp_path, "T")
    log.stamp = False
    log.append(kind, "s", {"note": PROBE, "reason": PROBE, "title": PROBE, "feedback": [PROBE]})
    shard = "".join(p.read_text() for p in (tmp_path / ".ddflow" / "events").glob("*.jsonl"))
    assert SECRET not in shard and "/home/zedd" not in shard and LAN not in shard
