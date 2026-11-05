"""The channels service: one relay through the ask engine, consent once per kind, settings to adapters.

The heart is GR-surface-parity's rule on this port: whatever adapter collects an answer, it lands
only through `engine.answer` under the caller's identity, so an agent is refused on every one.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ddflow.adapters import notify as N
from ddflow.adapters.fakes.env import FakeEnv
from ddflow.adapters.fakes.http import FakeHttpClient
from ddflow.adapters.fakes.notify import FakeHumanChannel
from ddflow.adapters.notify import Answer
from ddflow.config import Config
from ddflow.core.handlers.asks import asks_of
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import actors as A
from ddflow.services.ask import channels as C
from ddflow.services.ask import engine as E
from ddflow.services.ask import relay as R

URL = "https://hooks.example.test/in"
DIGEST = "d" * 64
HOOK = C.Settings(endpoints={"webhook": URL})


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", "CLAUDECODE"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def log(repo):
    return EventLog(repo, "asker")


def _person(cfg):
    return A.resolve_actor(cfg, "me")


def _agent(cfg):
    return A.resolve_actor(cfg, "bot-1", requested_agent="bot-1")


def _approval(log, cfg, **kw) -> str:
    return E.request(log, cfg, subject="s", text="ship it?", digest=DIGEST, **kw).id


def _row(log, ask_id):
    return asks_of(fold(log.read_all(), strict=False))[ask_id]


def _answers(log) -> list:
    return [e for e in log.read_all() if e.kind == "ask.answered"]


# -- relay --------------------------------------------------------------------------------------


def test_a_collected_answer_is_recorded_with_the_channel_as_how(log):
    cfg = Config()
    ask_id = _approval(log, cfg)
    got = R.relay(log, cfg, FakeHumanChannel(answers=["approved"]), ask_id, actor=_person(cfg))
    assert got.status == N.ANSWERED
    row = _row(log, ask_id)
    assert (row["outcome"], row["via"], row["answered_by"]) == ("approved", "fake", "me")


def test_the_channel_sees_the_rows_text_and_digest(log):
    cfg = Config()
    ask_id = _approval(log, cfg)
    f = FakeHumanChannel(answers=["denied"])
    R.relay(log, cfg, f, ask_id, actor=_person(cfg))
    (_, shown) = f.calls[0]
    assert (shown.ask_id, shown.text, shown.digest) == (ask_id, "ship it?", DIGEST)


def test_a_digest_the_person_did_not_see_is_refused_by_the_engine(log):
    cfg = Config()
    ask_id = _approval(log, cfg)
    wrong = Answer(N.ANSWERED, N.APPROVED, who="me", how="fake", digest="e" * 64)
    with pytest.raises(E.AskRefused, match="digest"):
        R.relay(log, cfg, FakeHumanChannel(answers=[wrong]), ask_id, actor=_person(cfg))
    assert not _answers(log)


def _terminal(cfg):
    return N.TerminalChannel(person="x", reader=lambda t: "approved", attempts=1, timeout_s=1.0)


def _fake(cfg):
    return FakeHumanChannel(answers=["approved"])


@pytest.mark.parametrize("build", [_terminal, _fake], ids=["terminal", "fake"])
def test_an_agent_identity_is_refused_on_every_channel_that_collects(log, build):
    cfg = Config()
    ask_id = _approval(log, cfg)
    with pytest.raises(E.AskRefused, match="person's act"):
        R.relay(log, cfg, build(cfg), ask_id, actor=_agent(cfg))
    assert not _answers(log) and _row(log, ask_id)["state"] == "pending"


@pytest.mark.parametrize("name", sorted(N.CHANNELS))
def test_no_registered_adapter_records_an_answer_for_an_agent(log, name, repo):
    """Every adapter by name, as an agent: nothing lands. Outbound ones are unavailable without
    consent, the bridge waits on the transcript, the terminal has no input here."""
    cfg = Config()
    ask_id = _approval(log, cfg)
    settings = C.Settings(endpoints={name: URL})
    channel = C.open_channel(log, cfg, name, settings=settings, repo=repo, http=FakeHttpClient())
    got = R.relay(log, cfg, channel, ask_id, actor=_agent(cfg))
    assert got.status in (N.PENDING, N.UNAVAILABLE)
    assert not _answers(log)


def test_a_channel_claiming_it_recorded_is_checked_against_the_log(log):
    cfg = Config()
    ask_id = _approval(log, cfg)
    lie = Answer(N.ANSWERED, N.APPROVED, who="me", how="fake", digest=DIGEST, recorded=True)
    with pytest.raises(E.AskRefused, match="recorded"):
        R.relay(log, cfg, FakeHumanChannel(answers=[lie]), ask_id, actor=_agent(cfg))


class _Writes(FakeHumanChannel):
    """A self-recording channel: the log gets ``outcome`` by ``via``, the caller told otherwise."""

    def __init__(self, log, cfg, outcome, via="fake", digest=DIGEST):
        super().__init__()
        self.ctx = (log, cfg, outcome, via, digest)

    def relay(self, approval):
        log, cfg, outcome, via, digest = self.ctx
        E.answer(log, cfg, approval.ask_id, outcome, actor=_person(cfg), digest=digest, via=via)
        return Answer(
            N.ANSWERED, N.APPROVED, who="liar", how="fake", digest="e" * 64, recorded=True
        )


def test_what_a_self_recording_channel_reports_is_read_back_from_the_log(log):
    """The channel says approved, the log says denied: the caller gets the log."""
    cfg = Config()
    ask_id = _approval(log, cfg)
    got = R.relay(log, cfg, _Writes(log, cfg, N.DENIED), ask_id, actor=_agent(cfg))
    assert (got.text, got.who, got.digest) == (N.DENIED, "me", DIGEST)


def test_a_row_another_channel_wrote_does_not_vouch_for_this_one(log):
    cfg = Config()
    ask_id = _approval(log, cfg)
    with pytest.raises(E.AskRefused, match="recorded"):
        R.relay(log, cfg, _Writes(log, cfg, N.APPROVED, via="cli"), ask_id, actor=_agent(cfg))


def test_a_pending_or_unavailable_answer_records_nothing(log):
    cfg = Config()
    ask_id = _approval(log, cfg)
    for status in (N.PENDING, N.UNAVAILABLE):
        f = FakeHumanChannel(answers=[Answer(status, how="fake")])
        assert R.relay(log, cfg, f, ask_id, actor=_person(cfg)).status == status
    assert not _answers(log)


def test_an_answered_or_unknown_ask_is_refused_before_the_channel_is_asked(log):
    cfg = Config()
    ask_id = _approval(log, cfg)
    R.relay(log, cfg, FakeHumanChannel(answers=["approved"]), ask_id, actor=_person(cfg))
    f = FakeHumanChannel(answers=["approved"])
    with pytest.raises(E.AskRefused, match="answered"):
        R.relay(log, cfg, f, ask_id, actor=_person(cfg))
    with pytest.raises(E.AskRefused, match="no ask"):
        R.relay(log, cfg, f, "Qnone", actor=_person(cfg))
    assert not f.calls


def test_a_question_goes_through_ask_with_its_options(log):
    cfg = Config()
    q = E.request(log, cfg, kind=E.QUESTION, subject="s", text="which?", options=("a", "b")).id
    f = FakeHumanChannel(answers=["b"])
    R.relay(log, cfg, f, q, actor=_person(cfg))
    assert f.calls == [("ask", "which?", ("a", "b"))]
    assert _row(log, q)["outcome"] == "b"


def test_a_free_text_question_keeps_the_text_out_of_the_outcome(log):
    cfg = Config()
    q = E.request(log, cfg, kind=E.QUESTION, subject="s", text="why?").id
    R.relay(log, cfg, FakeHumanChannel(answers=["because"]), q, actor=_person(cfg))
    row = _row(log, q)
    assert (row["outcome"], row["answer"]) == (E.ANSWERED_WORD, "because")


def test_the_one_time_code_reaches_the_channel_and_a_remote_answer_needs_it(log):
    cfg = Config()
    asked = E.request(log, cfg, subject="s", text="ship?", digest=DIGEST, with_code=True)
    f = FakeHumanChannel(answers=[Answer(N.PENDING, how="fake")])
    R.relay(log, cfg, f, asked.id, actor=_person(cfg), code=asked.code)
    assert f.calls[0][1].code == asked.code


# -- consent ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "kind"),
    [("slack", "chat"), ("teams", "chat"), ("webhook", "webhook"), ("n8n", "webhook"),
     ("openclaw", "webhook"), ("email", "email")],
)  # fmt: skip
def test_every_network_adapter_has_a_consent_kind(name, kind):
    assert C.KINDS[name] == kind


def test_the_kinds_cover_exactly_the_adapters_that_reach_the_network():
    assert set(C.KINDS) == set(N.CHANNELS) - {"terminal", "harness", "desktop"}


def test_without_consent_a_network_adapter_is_unavailable_and_sends_nothing(log, repo):
    http = FakeHttpClient()
    c = C.open_channel(log, Config(), "webhook", settings=HOOK, repo=repo, http=http)
    done = c.notify("x")
    assert done.status == N.UNAVAILABLE and "consent" in done.detail
    assert c.ask("y").status == N.UNAVAILABLE
    assert not http.calls


def _http():
    h = FakeHttpClient()
    h.when("POST", URL, status=200, body=b"ok")
    return h


def test_consent_is_once_per_kind_not_per_adapter_or_use(log, repo):
    settings = C.Settings(endpoints={"webhook": URL, "n8n": URL, "slack": URL})
    C.grant_consent(log, "webhook")
    http = _http()

    def build(name):
        return C.open_channel(log, Config(), name, settings=settings, repo=repo, http=http)

    assert build("webhook").notify("1").ok
    assert build("webhook").notify("2").ok
    assert build("n8n").notify("3").ok
    assert build("slack").notify("4").status == N.UNAVAILABLE


def test_an_agent_cannot_grant_consent(log, monkeypatch):
    from ddflow.services.approval import ApprovalRefused

    monkeypatch.setenv("DDFLOW_AGENT", "bot-1")
    with pytest.raises(ApprovalRefused):
        C.grant_consent(log, "webhook")
    assert not C.consented(fold(log.read_all(), strict=False), "webhook")


def test_a_kind_nobody_knows_cannot_be_granted(log):
    with pytest.raises(ValueError, match="consent kind"):
        C.grant_consent(log, "carrier-pigeon")


def test_an_endpoint_in_a_committed_layer_is_refused(log, repo):
    """A webhook URL is a secret: only the local layer or the environment may hold it."""
    C.grant_consent(log, "webhook")
    settings = C.Settings(endpoints={"webhook": URL}, committed=frozenset({"endpoints"}))
    http = FakeHttpClient()
    c = C.open_channel(log, Config(), "webhook", settings=settings, repo=repo, http=http)
    done = c.notify("x")
    assert done.status == N.UNAVAILABLE and "local" in done.detail and URL not in done.detail
    assert not http.calls


@pytest.mark.parametrize("field", C.ADDRESSES)
def test_a_mail_address_in_a_committed_layer_is_refused_too(log, repo, field):
    C.grant_consent(log, "email")
    settings = C.Settings(email_host="mx.example.test", committed=frozenset({field}))
    done = C.open_channel(log, Config(), "email", settings=settings, repo=repo).notify("x")
    assert done.status == N.UNAVAILABLE and field in done.detail


def test_the_token_comes_from_the_named_variable_and_never_from_config(log, repo):
    C.grant_consent(log, "webhook")
    settings = C.Settings(endpoints={"openclaw": URL}, token_env={"openclaw": "OC_TOKEN"})
    http = _http()
    env = FakeEnv({"OC_TOKEN": "t0k"})
    C.open_channel(
        log, Config(), "openclaw", settings=settings, repo=repo, http=http, env=env
    ).notify("x")
    assert http.calls[-1].headers["Authorization"] == "Bearer t0k"


def test_what_leaves_is_scrubbed_of_secrets(log, repo):
    C.grant_consent(log, "webhook")
    http = _http()
    secret = "ghp_" + "a1B2" * 9
    C.open_channel(log, Config(), "webhook", settings=HOOK, repo=repo, http=http).notify(
        f"key {secret}"
    )
    assert secret not in http.calls[-1].body.decode()


# -- the rest of the wiring ---------------------------------------------------------------------


def test_the_default_for_approvals_is_the_chat_bridge_not_the_terminal():
    """r-approval-chat-channel: an approval surfaces in the operator's chat."""
    assert (C.Settings().ask_channel, C.Settings().channel) == ("harness", "terminal")


def test_the_bridge_couriers_the_exact_approvals_ask_payload(log, repo):
    from ddflow.services.approvals import canon as K
    from ddflow.services.approvals import harness as H
    from ddflow.services.approvals import request as Q

    cfg = Config()
    filed = Q.file(
        log,
        cfg,
        fold(log.read_all(), strict=False),
        K.Canon("gate.skip.failed_review", "a1", "I1", "critic"),
    )
    channel = C.open_channel(log, cfg, "harness", repo=repo)
    got = R.relay(log, cfg, channel, filed.id, actor=_agent(cfg))
    assert got.status == N.PENDING
    assert got.courier == H.payload(_row(log, filed.id), cfg)


def test_the_bridge_records_through_attest_and_the_relay_does_not_answer_twice(
    log, repo, monkeypatch
):
    from ddflow.services.approvals import canon as K
    from ddflow.services.approvals import harness as H
    from ddflow.services.approvals import request as Q

    cfg = Config()
    filed = Q.file(
        log,
        cfg,
        fold(log.read_all(), strict=False),
        K.Canon("gate.skip.failed_review", "a1", "I1", "critic"),
    )
    yes = H.labels(filed.id)[0]
    hit = H.Attested("toolu_1", yes, "2026-11-05T00:00:00Z", "t.jsonl")
    monkeypatch.setattr(H, "find", lambda *a, **k: hit)
    channel = C.open_channel(log, cfg, "harness", repo=repo)
    got = R.relay(log, cfg, channel, filed.id, actor=_agent(cfg))
    assert got.status == N.ANSWERED and got.recorded and got.how == "harness"
    assert got.who == "harness:t" and got.proof == "toolu_1"
    assert len(_answers(log)) == 1


def test_the_desktop_goes_through_the_proc_it_is_given_and_is_scrubbed(log, repo):
    calls = []

    def capture(argv, *, timeout, **kw):
        calls.append(argv)
        return SimpleNamespace(returncode=0, error=None, stdout="", stderr="")

    secret = "ghp_" + "a1B2" * 9
    c = C.open_channel(log, Config(), "desktop", repo=repo, proc=SimpleNamespace(capture=capture))
    c.notify(f"key {secret}")
    assert calls and secret not in " ".join(calls[0])


def test_the_terminal_is_built_from_the_settings(log, repo):
    settings = C.Settings(ask_attempts=7, ask_timeout_s=9.0)
    c = C.open_channel(log, Config(), "terminal", settings=settings, repo=repo)
    assert (c.attempts, c.timeout_s) == (7, 9.0)


def test_email_is_built_from_the_settings(log, repo):
    C.grant_consent(log, "email")
    settings = C.Settings(email_host="mx.example.test", email_from="a@example.test",
                          email_to="b@example.test", email_password_env="MAIL_PW", email_user="u")  # fmt: skip
    env = FakeEnv({"MAIL_PW": "p"})
    c = C.open_channel(log, Config(), "email", settings=settings, repo=repo, env=env)
    assert (c.host, c.sender, c.to, c.user, c.password) == (
        "mx.example.test", "a@example.test", "b@example.test", "u", "p",
    )  # fmt: skip


def test_an_unknown_channel_name_names_those_known(log, repo):
    with pytest.raises(KeyError, match="terminal"):
        C.open_channel(log, Config(), "smoke-signals", repo=repo)
