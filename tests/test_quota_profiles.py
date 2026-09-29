"""Quota profiles: declared per agent or LLM, stored per user, operator wins (D-quotas)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ddflow.api import quota as A
from ddflow.services import quota as Q

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


@pytest.fixture(autouse=True)
def user_config(tmp_path, monkeypatch):
    """Every test gets its own user-level config dir: the real one is never touched."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    return tmp_path / "xdg" / "ddflow" / "quotas.json"


def test_the_store_is_user_level_and_honours_xdg(user_config):
    assert Q.store_path() == user_config


def test_a_declared_profile_round_trips(repo, user_config):
    out = A.quota_declare(
        repo, "agent:claude-code/abc123", windows="5h=percent,week=percent", note="Max 20x"
    )
    assert out.exit == OK, out.reason
    shown = A.quota_show(repo, "agent:claude-code/abc123")
    p = shown.data["profile"]
    assert p["state"] == "limited"
    assert [(w["window"], w["limit"], w["unit"]) for w in p["windows"]] == [
        ("5h", 100.0, "percent"),
        ("week", 100.0, "percent"),
    ]
    assert p["declared_by"] == "agent" and p["note"] == "Max 20x" and p["at"]
    assert json.loads(user_config.read_text())["version"] == Q.STORE_VERSION


def test_an_unlimited_llm_has_no_windows(repo):
    out = A.quota_declare(repo, "llm:http://10.220.230.8:8000/v1", unlimited=True)
    assert out.exit == OK, out.reason
    assert out.data["profile"]["state"] == "unlimited"
    assert out.data["profile"]["windows"] == []


def test_unknown_is_recorded_so_the_operator_can_be_asked(repo):
    assert A.quota_declare(repo, "agent:kilo/local", unknown=True).exit == OK
    listed = A.quota_list(repo).data
    assert [p["subject"] for p in listed["unknown"]] == ["agent:kilo/local"]


def test_undeclared_is_nothing_not_unlimited(repo):
    out = A.quota_show(repo, "agent:claude-code/never")
    assert out.exit == NOTHING and out.data["profile"] is None
    assert "ask the agent" in out.reason


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("day=2M:tokens", [("day", 2_000_000.0, "tokens", "rolling", "")]),
        ("week=40:sessions", [("week", 40.0, "sessions", "rolling", "")]),
        ("5h=1.5k:tokens", [("5h", 1500.0, "tokens", "rolling", "")]),
        (
            "month=50:usd@2026-10-01T00:00Z",
            [("month", 50.0, "usd", "fixed", "2026-10-01T00:00Z")],
        ),
        ("total=1G:tokens", [("total", 1e9, "tokens", "rolling", "")]),
    ],
)
def test_window_specs_parse(spec, expected):
    got = [(w.window, w.limit, w.unit, w.reset, w.anchor) for w in Q.parse_windows(spec)]
    assert got == expected


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"windows": "fortnight=1:tokens"}, "window 'fortnight'"),
        ({"windows": "day=1:bananas"}, "unit 'bananas'"),
        ({"windows": "day=0:tokens"}, "must be positive"),
        ({"windows": "day=lots:tokens"}, "not a number"),
        ({"windows": "day=5"}, "name the unit"),
        ({"windows": "day=1:tokens,day=2:tokens"}, "declared twice"),
        ({"windows": "total=1:tokens@2026-01-01"}, "never resets"),
        ({"windows": "month=1:usd@yesterday"}, "not an ISO-8601"),
        ({"windows": "month=1:usd@2026-10-01T00:00:00"}, "no timezone"),
        ({"windows": "5h=percent@2026-01-01T00:00Z"}, "takes no anchor"),
        ({"windows": "", "unlimited": False}, "exactly one of"),
        ({"windows": "day=1:tokens", "unlimited": True}, "exactly one of"),
        ({"unlimited": True, "unknown": True}, "exactly one of"),
        ({"unlimited": True, "by": "someone"}, "declared_by"),
    ],
)
def test_bad_declarations_fail_and_store_nothing(repo, user_config, kwargs, fragment):
    out = A.quota_declare(repo, "agent:x/y", **kwargs)
    assert out.exit == FAIL and fragment in out.reason, out.reason
    assert not user_config.exists()


def test_a_subject_must_say_what_kind_it_is(repo):
    out = A.quota_declare(repo, "claude", unlimited=True)
    assert out.exit == FAIL and "agent:<harness>/<account>" in out.reason


def test_an_agent_cannot_overwrite_the_operator(repo):
    """The agent is asked first; the operator decides."""
    s = "agent:claude-code/abc123"
    assert A.quota_declare(repo, s, windows="5h=percent", by="operator").exit == OK
    out = A.quota_declare(repo, s, unlimited=True, by="agent")
    assert out.exit == REFUSED and "ask the operator" in out.reason
    assert A.quota_show(repo, s).data["profile"]["state"] == "limited"
    # ...and the operator can change their own mind.
    assert A.quota_declare(repo, s, unlimited=True, by="operator").exit == OK
    assert A.quota_show(repo, s).data["profile"]["state"] == "unlimited"


def test_the_operator_can_overwrite_the_agent_and_the_replacement_is_reported(repo):
    s = "agent:kilo/local"
    A.quota_declare(repo, s, unknown=True)
    out = A.quota_declare(repo, s, windows="day=2M:tokens", by="operator")
    assert out.exit == OK and out.data["replaced"]["state"] == "unknown"


def test_a_corrupt_store_is_a_failure_never_no_quotas(repo, user_config):
    """Reading it as empty would lift every limit without a word."""
    user_config.parent.mkdir(parents=True)
    user_config.write_text("{not json")
    for out in (A.quota_list(repo), A.quota_show(repo, "agent:x/y")):
        assert out.exit == FAIL and "not read as 'no quotas'" in out.reason, out.reason
    assert A.quota_declare(repo, "agent:x/y", unlimited=True).exit == FAIL
    assert user_config.read_text() == "{not json", "a failed declare must not rewrite the store"


def test_an_unknown_store_version_is_refused(repo, user_config):
    user_config.parent.mkdir(parents=True)
    user_config.write_text(json.dumps({"version": 99, "profiles": {}}))
    assert A.quota_list(repo).exit == FAIL


def test_forget_makes_the_subject_undeclared_again(repo):
    s = "llm:http://h:8000/v1"
    A.quota_declare(repo, s, unlimited=True)
    assert A.quota_forget(repo, s).exit == OK
    assert A.quota_show(repo, s).exit == NOTHING
    assert A.quota_forget(repo, s).exit == NOTHING


def test_account_tags_hide_the_raw_id():
    """Pinned to the exact digest prefix (critic): a length check alone passed
    `raw[:12]`, which leaks the id's first twelve characters."""
    import hashlib

    raw = "d9c6b4dc-101d-4597-bf2a-96e954f2057c"
    tag = Q.account_tag(raw)
    assert tag == hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    assert not raw.startswith(tag) and tag not in raw


def test_an_agent_cannot_forget_the_operator_either(repo):
    """Rubber-duck on B-quota-profiles: forget-then-declare was a side door around the
    operator-wins rule."""
    s = "agent:claude-code/abc123"
    A.quota_declare(repo, s, windows="5h=percent", by="operator")
    out = A.quota_forget(repo, s)
    assert out.exit == REFUSED and "ask the operator" in out.reason
    assert A.quota_declare(repo, s, unlimited=True).exit == REFUSED
    assert A.quota_show(repo, s).data["profile"]["declared_by"] == "operator"
    assert A.quota_forget(repo, s, by="operator").exit == OK


def test_a_store_entry_filed_under_another_subject_is_a_failure(repo, user_config):
    """Rubber-duck: the key was trusted over the profile's own subject, so the real
    subject's operator quota read as undeclared."""
    A.quota_declare(repo, "agent:claude-code/real", unlimited=True, by="operator")
    doc = json.loads(user_config.read_text())
    doc["profiles"]["agent:claude-code/other"] = doc["profiles"].pop("agent:claude-code/real")
    user_config.write_text(json.dumps(doc))
    for out in (A.quota_show(repo, "agent:claude-code/real"), A.quota_list(repo)):
        assert out.exit == FAIL and "holds subject" in out.reason, out.reason
