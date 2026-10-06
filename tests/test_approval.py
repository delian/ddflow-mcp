"""B-uni-approval: one approve-by-digest primitive.

`grant` (a person, never an agent identity), `check` (the digest of what runs must be a
digest a person approved), `use` (a single-use approval's token works once), their fold,
and the first flow moved onto it: approving a tool-written reviewer.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.events import Event
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import approval as AP


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    """Nothing in the environment says this is an agent's shell."""
    for var in ("DDFLOW_AGENT", *AP.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


def _log(repo: Path) -> EventLog:
    return EventLog(repo, "person")


def _st(repo: Path):
    return fold(_log(repo).read_all())


# -- grant ----------------------------------------------------------------------------------


@pytest.mark.parametrize("how", ["arg", "env", "harness"])
def test_an_agent_identity_cannot_approve(repo, monkeypatch, how):
    kwargs = {}
    if how == "arg":
        kwargs["requested_agent"] = "bot"
    elif how == "env":
        monkeypatch.setenv("DDFLOW_AGENT", "bot")
    else:
        monkeypatch.setenv(AP.HARNESS_MARKERS[0], "1")
    with pytest.raises(AP.ApprovalRefused, match="person's act"):
        AP.grant(_log(repo), "skill:x", "d1", **kwargs)
    assert not _st(repo).approvals


def test_an_approval_needs_a_subject_and_a_digest(repo):
    for subject, digest in (("", "d"), ("s", "")):
        with pytest.raises(AP.ApprovalRefused):
            AP.grant(_log(repo), subject, digest)


def test_a_grant_records_who_and_where(repo):
    got = AP.grant(_log(repo), "skill:x", "d1", note="read it")
    row = _st(repo).approvals["skill:x"][0]
    assert row["digest"] == "d1" and row["human"] is True and row["note"] == "read it"
    assert row["user"] == got.actor.user and row["host"] == got.actor.host
    assert got.token == "" and row["token_hash"] == ""


# -- check ----------------------------------------------------------------------------------


def test_only_the_approved_digest_is_approved(repo):
    assert not AP.check(_st(repo), "skill:x", "d1").ok
    AP.grant(_log(repo), "skill:x", "d1")
    st = _st(repo)
    assert AP.check(st, "skill:x", "d1").ok
    later = AP.check(st, "skill:x", "d2")
    assert not later.ok and "changed since it was approved" in later.reason
    assert not AP.check(st, "skill:y", "d1").ok  # another subject


def test_an_earlier_approval_still_answers_for_its_own_digest(repo):
    AP.grant(_log(repo), "skill:x", "d1")
    AP.grant(_log(repo), "skill:x", "d2")
    st = _st(repo)
    assert AP.check(st, "skill:x", "d1").ok and AP.check(st, "skill:x", "d2").ok


# -- single use -----------------------------------------------------------------------------


def test_a_single_use_approval_works_once_and_only_with_its_token(repo):
    got = AP.grant(_log(repo), "upstream:B1", "d1", single_use=True)
    assert got.token
    st = _st(repo)
    assert not AP.check(st, "upstream:B1", "d1").ok  # no token
    assert not AP.check(st, "upstream:B1", "d1", token="guess").ok
    assert not AP.check(st, "upstream:B1", "d2", token=got.token).ok  # other text
    assert AP.use(_log(repo), st, "upstream:B1", "d1", got.token).ok
    again = AP.use(_log(repo), _st(repo), "upstream:B1", "d1", got.token)
    assert not again.ok and "spent" in again.reason


def test_two_uses_from_one_stale_view_spend_the_token_once(repo):
    """Two agents holding the same fold present one token at once: the second is
    re-checked under the log's lock and refused."""
    got = AP.grant(_log(repo), "upstream:B1", "d1", single_use=True)
    stale = _st(repo)
    assert AP.use(_log(repo), stale, "upstream:B1", "d1", got.token).ok
    assert not AP.use(_log(repo), stale, "upstream:B1", "d1", got.token).ok
    used = [e for e in _log(repo).read_all() if e.kind == "approval.used"]
    assert len(used) == 1


def test_the_token_itself_is_never_written(repo):
    got = AP.grant(_log(repo), "upstream:B1", "d1", single_use=True)
    shards = (repo / ".ddflow" / "events").glob("*.jsonl")
    assert not any(got.token in p.read_text() for p in shards)
    assert _st(repo).approvals["upstream:B1"][0]["token_hash"] == AP.token_hash(got.token)


# -- the reviewer flow, moved onto the primitive --------------------------------------------

HTTP = """
[[reviewer]]
name = "lan"
kind = "openai"
base_url = "http://127.0.0.1:9/v1"
model = "m"
family = "deepseek"
"""


def test_approving_a_reviewer_is_an_approval_of_its_digest(repo):
    from ddflow.api import setup as AS
    from ddflow.services import reviewer_trust as RT

    assert AS.configure(repo, AS.ConfigEdit(append_toml=HTTP, local=True)).exit == 0
    code, out, err = run_cli(repo, "reviewers", "approve", "lan", "--note", "checked")
    assert code == 0, (out, err)
    dig = RT.digest_of(repo, "lan")
    st = fold(EventLog(repo, "x").read_all())
    assert AP.check(st, "reviewer:lan", dig).ok
    assert st.reviewer_approvals[dig]["note"] == "checked"  # what the trust checks read
    kinds = [e.kind for e in EventLog(repo, "x").read_all()]
    assert "approval.granted" in kinds and "reviewer.approved" not in kinds


def test_a_reviewer_approval_from_before_the_primitive_still_counts():
    """A log written before B-uni-approval holds `reviewer.approved`: it answers the one
    check and the trust table alike."""
    ev = Event(
        kind="reviewer.approved",
        subject="lan",
        data={"digest": "abc", "user": "op", "host": "h", "note": "n", "human": True},
        agent="op",
        lamport=1,
        ts="t",
    )
    st = fold([ev])
    assert AP.check(st, "reviewer:lan", "abc").ok
    assert st.reviewer_approvals["abc"]["user"] == "op"
