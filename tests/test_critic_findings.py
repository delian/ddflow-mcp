"""Regression tests for nine defects found by a cross-family (Qwen/Alibaba) critic.

All nine were probed before any code changed, and all nine reproduced. Five are one
bug CLASS — the fold applies an event without checking it belongs to the current
lease/record — and the earlier fix for that class had patched exactly one handler and
left its siblings, which is the incomplete-fix failure the rule against it names.

Reordering is not hypothetical here: per-agent shards are merged by git across branches
and clones, and the total order is `(lamport, agent, id)`. Lamport values computed on
unsynced clones do not preserve real-time causality across a merge, so a stale event
CAN sort after a newer one. Every test below constructs exactly that.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orchard.config import Config, _coerce
from orchard.core.model import fold
from orchard.infra.log import Event
from orchard.services import sessions as S


def ev(lamport: int, agent: str, kind: str, subject: str, data: dict) -> Event:
    e = Event(
        kind=kind,
        subject=subject,
        data=data,
        agent=agent,
        lamport=lamport,
        ts=f"2026-01-01T00:00:{lamport:02d}Z",
    )
    return Event(**{**e.__dict__, "id": e.compute_id()})


# -- the fold-ordering class ----------------------------------------------------------


def test_a_stale_release_cannot_end_the_current_holders_lease():
    """A's release, reordered after B's acquisition, used to free B's item while B was
    still editing it — another agent could then claim and edit the same files."""
    st = fold(
        [
            ev(1, "c", "task.added", "T", {"parent": "P"}),
            ev(2, "A", "lease.acquired", "T", {"holder": "A", "at": 1.0, "ttl_s": 999}),
            ev(
                3,
                "B",
                "lease.acquired",
                "T",
                {"holder": "B", "at": 2.0, "ttl_s": 999, "worktree": "wtB"},
            ),
            ev(4, "A", "lease.released", "T", {"holder": "A"}),
        ]
    )
    lease = st.items["T"].lease
    assert lease is not None, "a former holder's release destroyed the live lease"
    assert lease.holder == "B" and lease.worktree == "wtB"


def test_a_release_by_the_actual_holder_still_works():
    """The guard must not break the ordinary case."""
    st = fold(
        [
            ev(1, "c", "task.added", "T", {"parent": "P"}),
            ev(2, "A", "lease.acquired", "T", {"holder": "A", "at": 1.0, "ttl_s": 999}),
            ev(3, "A", "lease.released", "T", {"holder": "A"}),
        ]
    )
    assert st.items["T"].lease is None


def test_a_renewal_never_moves_the_clock_backward():
    """A stale renewal set renewed_at to its own older timestamp, so a live lease read
    as expired and invited another agent to take an item under active edit."""
    st = fold(
        [
            ev(1, "c", "task.added", "T", {"parent": "P"}),
            ev(
                2,
                "A",
                "lease.acquired",
                "T",
                {"holder": "A", "at": 200.0, "ttl_s": 100, "worktree": "new"},
            ),
            ev(3, "A", "lease.renewed", "T", {"holder": "A", "at": 100.0, "worktree": "old"}),
        ]
    )
    lease = st.items["T"].lease
    assert lease.renewed_at == 200.0, "the lease clock went backward"
    assert lease.worktree == "new", "a stale renewal repointed the live lease"
    assert not lease.expired(250.0), "a live lease reads as expired"


def test_an_expired_lease_is_kept_so_recovery_can_see_its_worktree():
    """Deleting the lease on expiry lost the worktree pointer, so `expired_leases()`
    could never report an expiry and a cleanup pass saw an item with no lease at all."""
    st = fold(
        [
            ev(1, "c", "task.added", "T", {"parent": "P"}),
            ev(
                2,
                "A",
                "lease.acquired",
                "T",
                {"holder": "A", "at": 0.0, "ttl_s": 10, "worktree": "wtA"},
            ),
            ev(3, "A", "lease.expired", "T", {"holder": "A"}),
        ]
    )
    assert not st.active_leases(30.0), "an expired lease still counts as active"
    expired = st.expired_leases(30.0)
    assert "T" in expired, "expiry became invisible to expired_leases()"
    assert expired["T"].worktree == "wtA", "the worktree pointer was lost"


def test_session_started_merges_rather_than_replacing():
    """A reordered shard can deliver a prompt before its session.started; replacing the
    object dropped an event that IS in the append-only log."""
    st = fold(
        [
            ev(2, "A", "session.prompt", "S", {"text": "hello"}),
            ev(3, "A", "session.started", "S", {"model": "x"}),
        ]
    )
    assert [p["text"] for p in st.sessions["S"].prompts] == ["hello"]
    assert st.sessions["S"].model == "x"


def test_bug_found_after_bug_fixed_does_not_reopen_it():
    """A fixed bug read as open, losing the regression test that closed it."""
    st = fold(
        [
            ev(2, "A", "bug.fixed", "B", {"regression_test": "test_x"}),
            ev(3, "A", "bug.found", "B", {"summary": "crash"}),
        ]
    )
    bug = st.bugs["B"]
    assert not bug.open, "a fixed bug reads as open"
    assert bug.regression_test == "test_x"
    assert bug.summary == "crash", "the merge lost the summary"


# -- configuration --------------------------------------------------------------------


def test_a_typod_config_section_is_an_error_not_a_silent_drop():
    """`[leases]` for `[lease]` left every knob at its default while the operator
    believed the file was in effect — the silent-knob-drop class, in the module written
    to prevent it."""
    cfg = Config()
    with pytest.raises(ValueError, match=r"unknown config section \[leases\]"):
        cfg._apply({"leases": {"ttl_s": 1}}, "file")


def test_the_typo_error_suggests_the_intended_section():
    cfg = Config()
    with pytest.raises(ValueError, match=r"Did you mean \[lease\]"):
        cfg._apply({"leases": {"ttl_s": 1}}, "file")


def test_foreign_tables_are_not_mistaken_for_typos():
    """`[gate.*]` and `[[reviewer]]` live in the same file and are read by other
    loaders; erroring on them would make the one-config-file design impossible."""
    cfg = Config()
    cfg._apply({"gate": {"unit_tests": {"command": "x"}}, "reviewer": [{"name": "r"}]}, "file")


def test_an_env_list_can_carry_commas_via_json():
    """The plain comma-separated form tears `{16,}` in half; JSON is the way out."""
    got = _coerce(r'["(?i)\\b(sk-[A-Za-z0-9]{16,})\\b", "token=x"]', "list[str]")
    assert len(got) == 2, f"JSON list was not parsed as one element per entry: {got}"
    re.compile(got[0])  # a torn half would not compile
    assert "{16,}" in got[0], f"the quantifier was damaged: {got[0]!r}"


def test_the_plain_env_list_form_still_works_for_simple_values():
    assert _coerce("implement,merge", "list[str]") == ["implement", "merge"]
    assert _coerce("solo", "list[str]") == ["solo"]


def test_a_malformed_env_dict_entry_is_an_error():
    """Silently dropping a pair weakens whatever reads the map — for `agent.families`
    that is the reviewer-independence check itself."""
    with pytest.raises(ValueError, match="malformed dict entry"):
        _coerce("claude=anthropic,gpt", "dict[str, str]")


# -- redaction: a security control writing to a COMMITTED log -------------------------


@pytest.mark.parametrize(
    "secret,text",
    [
        (
            "abcdefghijklmnopqrstuvwxyz123456",
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456",
        ),
        ("ghp_abcdefghijklmnopqrst", "use ghp_abcdefghijklmnopqrst please"),
        ("sk-abcdefghijklmnop1234", "key is sk-abcdefghijklmnop1234"),
        ("xoxb-1234567890-abcdef", "slack xoxb-1234567890-abcdef"),
        ("AKIAIOSFODNN7EXAMPLE", "aws AKIAIOSFODNN7EXAMPLE here"),
        ("hunter2hunter2", "password: hunter2hunter2"),
    ],
)
def test_common_secret_shapes_are_redacted(secret, text):
    clean, n = S.redact(text, Config())
    assert secret not in clean, f"{secret!r} survived redaction into a committed log"
    assert n >= 1


def test_the_whole_pem_block_is_redacted_not_just_its_header():
    """Matching the header alone left the base64 body — the actual secret."""
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEAsecretbodyhere\n"
        "-----END RSA PRIVATE KEY-----"
    )
    clean, _ = S.redact(f"here it is:\n{pem}\nthanks", Config())
    assert "MIIEowIBAAKCAQEAsecretbodyhere" not in clean
    assert "here it is" in clean and "thanks" in clean


def test_redaction_keeps_the_surrounding_sentence_readable():
    clean, _ = S.redact("Deploy with Bearer abcdefghijklmnop and then restart", Config())
    assert "Deploy with" in clean and "and then restart" in clean
    assert "Bearer [REDACTED]" in clean


def test_an_invalid_redaction_pattern_raises_rather_than_silently_not_matching():
    """The failure mode of a security control must not be silence: a pattern that does
    not compile simply stops matching, and secrets flow into git while the config still
    lists the rule that was meant to stop them."""
    cfg = Config()
    cfg.session.redact_patterns = ["(unclosed"]
    with pytest.raises(ValueError, match="does not compile"):
        S.redact("anything", cfg)


def test_the_invalid_pattern_error_names_the_json_remedy():
    """The commonest cause is comma-splitting an env var; the message must say so."""
    cfg = Config()
    cfg.session.redact_patterns = _coerce(r"(?i)\b(sk-[A-Za-z0-9]{16,})\b", "list[str]")
    with pytest.raises(ValueError, match="JSON"):
        S.redact("x", cfg)
