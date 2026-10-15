"""SE-ask-core: the approvals-and-questions engine (request, answer, digest, code, expiry).

The human-only invariant is the heart: an agent identity and the MCP surface can ask and read,
never record an answer; a bot answers only what the operator marked bot-answerable.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ddflow.config import Config
from ddflow.core.handlers.asks import asks_of
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import actors as A
from ddflow.services.ask import engine as E

ROOT = Path(__file__).resolve().parents[1]
T0 = 1_800_000_000.0


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", "CLAUDECODE"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def log(repo):
    return EventLog(repo, "asker")


def _person(cfg):
    return A.resolve_actor(cfg, "me")


def _agent(cfg, via_mcp=False):
    return A.resolve_actor(cfg, "bot-1", requested_agent="bot-1", via_mcp=via_mcp)


def _asker(cfg):
    return A.resolve_actor(cfg, "asker", requested_agent="asker")


def _channel(cfg, role="operator", ident="gw"):
    return A.resolve_actor(cfg, ident, credential_role=role, authenticated=True)


def _state(log):
    return fold(log.read_all(), strict=False)


def _ask(log, cfg, **kw):
    kw.setdefault("subject", "send:draft-1")
    kw.setdefault("content", "hello world")
    kw.setdefault("now", T0)
    return E.request(log, cfg, **kw)


# -- the human-only invariant ---------------------------------------------------------------


@pytest.mark.parametrize("via_mcp", [False, True])
def test_an_agent_and_the_mcp_surface_can_ask_and_read_but_never_answer(log, via_mcp):
    cfg = Config()
    ask = _ask(log, cfg)
    agent = _agent(cfg, via_mcp)
    with pytest.raises(E.AskRefused, match="person's act"):
        E.answer(log, cfg, ask.id, "approved", actor=agent, digest=E.digest_of("hello world"), now=T0)
    assert E.pending(_state(log), T0)[0]["id"] == ask.id  # read works
    assert not [e for e in log.read_all() if e.kind == "ask.answered"]


def test_an_agent_cannot_mark_its_own_ask_bot_answerable(log):
    cfg = Config()
    with pytest.raises(E.AskRefused, match="bot-answerable"):
        _ask(log, cfg, bot_answerable=True, actor=_agent(cfg))
    assert not asks_of(_state(log))


def test_a_credentialed_agent_role_still_cannot_answer(log):
    cfg = Config()
    ask = _ask(log, cfg)
    actor = _channel(cfg, role="agent")  # a proven id, but the role holds no approve
    with pytest.raises(E.AskRefused):
        E.answer(log, cfg, ask.id, "approved", actor=actor, digest=E.digest_of("hello world"), now=T0)


def test_a_bot_answers_only_what_is_marked_bot_answerable(log):
    cfg = Config()
    plain = _ask(log, cfg, subject="a")
    marked = _ask(log, cfg, subject="b", bot_answerable=True, actor=_person(cfg))
    bot = _channel(cfg, role="bot", ident="openclaw")
    d = E.digest_of("hello world")
    with pytest.raises(E.AskRefused):
        E.answer(log, cfg, plain.id, "approved", actor=bot, digest=d, now=T0)
    row = E.answer(log, cfg, marked.id, "approved", actor=bot, digest=d, now=T0)
    assert row["state"] == "answered" and row["human"] is False
    assert E.verdict(_state(log), "b", d, T0).ok  # bot-answerable: counts
    assert not E.verdict(_state(log), "a", d, T0).ok


def test_no_mcp_or_api_module_can_record_an_answer():
    """Structural: only the engine writes `ask.answered`, and no agent-facing module imports it."""
    writers = [
        p.relative_to(ROOT).as_posix()
        for p in (ROOT / "ddflow").rglob("*.py")
        if re.search(r"[\"']ask\.answered[\"']", p.read_text(encoding="utf-8"))
    ]
    assert sorted(writers) == ["ddflow/core/handlers/asks.py", "ddflow/services/ask/engine.py"]
    for sub in ("surfaces/mcp.py", "surfaces/tools", "api"):
        target = ROOT / "ddflow" / sub
        if not target.exists():
            continue
        for p in target.rglob("*.py") if target.is_dir() else [target]:
            assert "services.ask" not in p.read_text(encoding="utf-8").replace("services import ask", "services.ask"), p


# -- digest ---------------------------------------------------------------------------------


def test_an_answer_must_present_the_digest_it_saw(log):
    cfg = Config()
    ask = _ask(log, cfg)
    with pytest.raises(E.AskRefused, match="digest"):
        E.answer(log, cfg, ask.id, "approved", actor=_person(cfg), digest="wrong", now=T0)
    E.answer(log, cfg, ask.id, "approved", actor=_person(cfg), digest=E.digest_of("hello world"), now=T0)
    st_ = _state(log)
    assert E.verdict(st_, "send:draft-1", E.digest_of("hello world"), T0).ok
    edited = E.verdict(st_, "send:draft-1", E.digest_of("hello world!"), T0)
    assert not edited.ok and "changed since" in edited.reason


def test_a_denial_does_not_approve(log):
    cfg = Config()
    ask = _ask(log, cfg)
    d = E.digest_of("hello world")
    E.answer(log, cfg, ask.id, "denied", actor=_person(cfg), digest=d, now=T0)
    assert not E.verdict(_state(log), "send:draft-1", d, T0).ok


def test_an_approval_needs_something_to_approve(log):
    with pytest.raises(E.AskRefused, match="digest or its content"):
        E.request(log, Config(), subject="x")


# -- one-time code --------------------------------------------------------------------------


def test_the_code_is_kept_as_a_hash_and_a_channel_must_present_it(log):
    cfg = Config()
    ask = _ask(log, cfg, with_code=True)
    assert len(ask.code) == cfg.agent.ask_code_length
    assert ask.code not in "".join(str(e.data) for e in log.read_all())
    d = E.digest_of("hello world")
    ch = _channel(cfg)
    for bad in ("", "WRONGCODE"):
        with pytest.raises(E.AskRefused, match="one-time code"):
            E.answer(log, cfg, ask.id, "approved", actor=ch, digest=d, code=bad, now=T0)
    E.answer(log, cfg, ask.id, "approved", actor=ch, digest=d, code=ask.code, now=T0)
    with pytest.raises(E.AskRefused, match="already"):  # and it works once
        E.answer(log, cfg, ask.id, "approved", actor=ch, digest=d, code=ask.code, now=T0)


def test_a_person_at_the_terminal_needs_no_code(log):
    cfg = Config()
    ask = _ask(log, cfg, with_code=True)
    E.answer(log, cfg, ask.id, "approved", actor=_person(cfg), digest=E.digest_of("hello world"), now=T0)


def test_the_code_length_is_a_knob(log):
    cfg = Config()
    cfg.agent.ask_code_length = 12
    assert len(_ask(log, cfg, with_code=True).code) == 12


# -- expiry ---------------------------------------------------------------------------------


def test_an_ask_expires_and_the_ttl_is_a_knob(log):
    cfg = Config()
    cfg.agent.ask_ttl_s = 60
    ask = _ask(log, cfg)
    d = E.digest_of("hello world")
    with pytest.raises(E.AskRefused, match="expired"):
        E.answer(log, cfg, ask.id, "approved", actor=_person(cfg), digest=d, now=T0 + 61)
    assert E.pending(_state(log), T0 + 61) == []
    assert E.sweep(log, T0 + 61) == [ask.id]
    assert E.sweep(log, T0 + 62) == []
    assert asks_of(_state(log))[ask.id]["state"] == "expired"


def test_an_ask_that_could_never_be_answered_is_refused(log):
    cfg = Config()
    cfg.agent.ask_ttl_s = 0
    with pytest.raises(E.AskRefused, match="expire"):
        _ask(log, cfg)


# -- questions, withdrawal ------------------------------------------------------------------


def test_a_question_takes_one_of_its_options_or_free_text(log):
    cfg = Config()
    q = E.request(log, cfg, subject="which?", text="a or b?", kind="question", options=["a", "b"], now=T0)
    with pytest.raises(E.AskRefused, match="one of"):
        E.answer(log, cfg, q.id, "c", actor=_person(cfg), now=T0)
    E.answer(log, cfg, q.id, "b", actor=_person(cfg), text="because", now=T0)
    free = E.request(log, cfg, subject="why?", text="why?", kind="question", now=T0)
    row = E.answer(log, cfg, free.id, "see text", actor=_person(cfg), text="x", now=T0)
    assert row["state"] == "answered"


def test_the_asker_can_withdraw_a_pending_ask_only(log):
    cfg = Config()
    ask = _ask(log, cfg)
    E.withdraw(log, ask.id, actor=_asker(cfg), now=T0)
    assert asks_of(_state(log))[ask.id]["state"] == "withdrawn"
    with pytest.raises(E.AskRefused):
        E.withdraw(log, ask.id, actor=_asker(cfg), now=T0)
    other = _ask(log, cfg, subject="o")
    with pytest.raises(E.AskRefused, match="may withdraw"):
        E.withdraw(log, other.id, actor=A.resolve_actor(cfg, "stranger", requested_agent="stranger"), now=T0)
    E.withdraw(log, other.id, actor=_person(cfg), now=T0)  # an approver may
    with pytest.raises(E.AskRefused, match="already withdrawn"):
        E.answer(log, cfg, ask.id, "approved", actor=_person(cfg), digest=E.digest_of("hello world"), now=T0)


def test_a_question_needs_its_text_and_flags_are_real_booleans(log):
    cfg = Config()
    with pytest.raises(E.AskRefused, match="text"):
        E.request(log, cfg, subject="Which region?", kind="question", now=T0)
    with pytest.raises(E.AskRefused, match="true or false"):
        _ask(log, cfg, bot_answerable="false", actor=_person(cfg))
    log.append("ask.requested", "Qx", {"id": "Qx", "subject": "s", "digest": "d", "bot_answerable": "false", "expires_at": "2099-01-01T00:00:00Z"})
    assert asks_of(_state(log))["Qx"]["bot_answerable"] is False


# -- replay ---------------------------------------------------------------------------------


@settings(max_examples=40, deadline=None)
@given(
    steps=st.lists(
        st.tuples(
            st.sampled_from(["ask", "approve", "deny", "withdraw", "agent_try"]),
            st.integers(0, 3),
        ),
        max_size=14,
    )
)
def test_replaying_the_events_restores_the_same_pending_and_answered_state(tmp_path_factory, steps):
    cfg = Config()
    root = tmp_path_factory.mktemp("p")
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    log = EventLog(root, "asker")
    ids: list[str] = []
    d = E.digest_of("c")
    for op, i in steps:
        try:
            if op == "ask":
                ids.append(E.request(log, cfg, subject=f"s{i}", content="c", now=T0).id)
            elif ids and op in ("approve", "deny"):
                E.answer(log, cfg, ids[i % len(ids)], "approved" if op == "approve" else "denied",
                         actor=_person(cfg), digest=d, now=T0)
            elif ids and op == "withdraw":
                E.withdraw(log, ids[i % len(ids)], actor=_asker(cfg), now=T0)
            elif ids and op == "agent_try":
                E.answer(log, cfg, ids[i % len(ids)], "approved", actor=_agent(cfg), digest=d, now=T0)
        except E.AskRefused:
            pass
    events = log.read_all()
    live = asks_of(_state(log))
    # Replay: the same kind/subject/data appended into a FRESH log (new ids and times) folds to
    # the same pending/answered state; the fold keeps nothing but what the events say.
    other_root = tmp_path_factory.mktemp("q")
    subprocess.run(["git", "init", "-q", "-b", "main", str(other_root)], check=True)
    other = EventLog(other_root, "asker")
    for ev in events:
        other.append(ev.kind, ev.subject, dict(ev.data))
    replayed = asks_of(_state(other))
    keep = ("id", "state", "outcome", "human", "subject", "digest", "requested_by", "answered_by")
    project = lambda rows: {k: {f: r[f] for f in keep} for k, r in rows.items()}  # noqa: E731
    assert project(replayed) == project(live)
    # no agent attempt ever landed: every recorded answer is a person's
    assert all(r["human"] for r in live.values() if r["state"] == "answered")
    # status is a function of the rows alone
    assert {k: E.status(r, T0) for k, r in replayed.items()} == {k: E.status(r, T0) for k, r in live.items()}
