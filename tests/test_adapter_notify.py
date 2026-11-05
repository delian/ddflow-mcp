"""The `HumanChannel` port: the terminal adapter and the fake pass the same cases; every other
adapter is held to what it can do (`CONFORMANCE` is read by tests/test_ports_contract.py).

No test here reaches a network, a desktop or a mail server: HTTP is `FakeHttpClient`, a process a
scripted runner, SMTP a recording stand-in.
"""

from __future__ import annotations

import io
import json
import os
import smtplib
import time
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from ddflow.adapters import notify as N
from ddflow.adapters import notify_outbound as O
from ddflow.adapters.fakes import FAKES
from ddflow.adapters.fakes.http import FakeHttpClient
from ddflow.adapters.fakes.notify import FakeHumanChannel
from ddflow.adapters.notify import Answer, Approval, HumanChannel

URL = "https://hooks.example.test/in"
APPROVAL = Approval("Q1", "ship it?", "d" * 64)


def _lines(*lines: str):
    """A terminal reader that hands out ``lines``, then says the input ended."""
    left = list(lines)
    return lambda timeout: left.pop(0) if left else None


def _terminal(*lines: str, attempts: int = 3) -> N.TerminalChannel:
    return N.TerminalChannel(
        person="me", reader=_lines(*lines), out=io.StringIO(), attempts=attempts, timeout_s=1.0
    )


class _Broken(io.StringIO):
    """A stream whose reader went away."""

    def write(self, text: str) -> int:
        raise BrokenPipeError(text)


def channel_cases(c) -> None:
    """Scripted with the answers `yes`, `2`, `nope`, `free`, `approved`, then nothing."""
    # notify: delivered, and it says where
    done = c.notify("the build is green")
    assert done.ok and done.status == N.DELIVERED and done.channel == c.name
    # a severity outside the set is the caller's bug on every adapter, not a silent "info"
    with pytest.raises(ValueError, match="severity"):
        c.notify("x", "shouty")
    # ask: an option by its label, with who answered and how
    got = c.ask("go?", ("yes", "no"))
    assert (got.status, got.text, got.how) == (N.ANSWERED, "yes", c.name)
    assert got.who
    # ask: an option by its number
    assert c.ask("which?", ("a", "b")).text == "b"
    # ask: an answer that is none of the options is not taken for one
    assert c.ask("go?", ("yes", "no")).text == "no"
    # ask: free text when there are no options
    assert c.ask("why?").text == "free"
    # relay: the outcome, and the digest the person was shown
    got = c.relay(APPROVAL)
    assert (got.status, got.text, got.digest) == (N.ANSWERED, N.APPROVED, APPROVAL.digest)
    # nobody there: unavailable, never an answer
    gone = c.ask("still there?", ("yes", "no"))
    assert gone.status == N.UNAVAILABLE and not gone.text and gone.detail
    assert c.relay(APPROVAL).status == N.UNAVAILABLE


def _real(monkeypatch) -> N.TerminalChannel:
    return _terminal("yes", "2", "nope", "no", "free", "approved")


def _fake(cls) -> FakeHumanChannel:
    return cls(answers=["yes", "2", "nope", "no", "free", "approved"])


CONFORMANCE = {"HumanChannel": {"real": _real, "fake": _fake, "cases": channel_cases}}


def test_every_adapter_and_the_fake_satisfy_the_port():
    assert FAKES["HumanChannel"] is FakeHumanChannel
    assert isinstance(FakeHumanChannel(), HumanChannel)
    for name in N.CHANNELS:
        assert issubclass(N.CHANNELS[name], HumanChannel), name


def test_the_registry_names_every_adapter_the_item_lists():
    assert set(N.CHANNELS) == {
        "terminal", "harness", "desktop", "email", "slack", "teams", "webhook", "n8n", "openclaw",
    }  # fmt: skip
    for name in N.CHANNELS:
        assert N.CHANNELS[name].name == name


def test_importing_the_port_loads_no_network_module():
    """The outbound adapters are lazy: a caller of the terminal pays for no `smtplib`."""
    import subprocess
    import sys

    code = "import sys, ddflow.adapters.notify; print('smtplib' in sys.modules)"
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert done.stdout.strip() == "False"


def test_an_answer_is_a_value():
    with pytest.raises(FrozenInstanceError):
        Answer(N.ANSWERED).text = "x"  # type: ignore[misc]


def test_status_words_match_the_engine():
    from ddflow.services.ask import engine as E

    assert (N.APPROVED, N.DENIED) == (E.APPROVED, E.DENIED)


# -- terminal -----------------------------------------------------------------------------------


def test_the_terminal_writes_to_its_stream_and_shows_the_digest():
    out = io.StringIO()
    c = N.TerminalChannel(person="me", reader=_lines("denied"), out=out, attempts=1, timeout_s=1.0)
    got = c.relay(Approval("Q9", "drop the table?", "abc123"))
    assert got.text == N.DENIED
    shown = out.getvalue()
    assert "drop the table?" in shown and "Q9" in shown and "abc123" in shown


def test_the_terminal_strips_control_characters_from_what_it_shows():
    """The text may come from an agent: an escape sequence must not repaint what the person reads."""
    out = io.StringIO()
    c = N.TerminalChannel(person="me", reader=_lines("x"), out=out, attempts=1, timeout_s=1.0)
    c.ask("safe\x1b[2K\x1b[1Ahidden\rline")
    assert "\x1b" not in out.getvalue() and "\r" not in out.getvalue()


def test_the_terminal_gives_up_after_its_attempts():
    c = _terminal("nope", "nope", "yes", attempts=2)
    got = c.ask("go?", ("yes", "no"))
    assert got.status == N.UNAVAILABLE and "2" in got.detail


def test_the_terminal_refuses_an_empty_free_text_answer():
    assert _terminal("   ", attempts=1).ask("why?").status == N.UNAVAILABLE


def test_the_default_reader_never_reads_a_stdin_that_is_no_terminal(monkeypatch):
    """Over MCP stdin IS the JSON-RPC session: reading it would eat the protocol's bytes."""
    piped = io.StringIO("approved\n")
    monkeypatch.setattr("sys.stdin", piped)
    c = N.TerminalChannel(person="me", out=io.StringIO(), attempts=1, timeout_s=0.01)
    assert c.ask("go?", ("yes", "no")).status == N.UNAVAILABLE
    assert piped.tell() == 0


def test_the_default_output_is_stderr_never_the_protocols_stdout(capsys):
    N.TerminalChannel(person="me", reader=_lines(), attempts=1, timeout_s=0.01).notify("hello")
    got = capsys.readouterr()
    assert not got.out and "hello" in got.err


def test_a_closed_terminal_is_unavailable_not_an_exception():
    c = N.TerminalChannel(
        person="me", reader=_lines("yes"), out=_Broken(), attempts=1, timeout_s=1.0
    )
    assert c.notify("x").status == N.UNAVAILABLE
    assert c.ask("y").status == N.UNAVAILABLE


def test_a_terminal_with_no_stderr_never_falls_back_to_stdout(capsys, monkeypatch):
    """`print(file=None)` writes to stdout, which over MCP is the protocol."""
    monkeypatch.setattr("sys.stderr", None)
    c = N.TerminalChannel(person="me", reader=_lines("yes"), attempts=1, timeout_s=1.0)
    assert c.notify("x").status == N.UNAVAILABLE
    assert c.ask("y", ("yes",)).status == N.UNAVAILABLE
    assert not capsys.readouterr().out


def test_a_closed_or_narrow_stream_is_a_status_not_an_exception():
    closed = io.StringIO()
    closed.close()
    ascii_only = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    for out, text in ((closed, "x"), (ascii_only, "caf\u00e9")):
        c = N.TerminalChannel(person="me", reader=_lines("yes"), out=out, attempts=1, timeout_s=1.0)
        assert c.notify(text).status == N.UNAVAILABLE
        assert c.ask(text).status == N.UNAVAILABLE


def test_bidi_overrides_never_reach_the_person():
    """A right-to-left override could show a digest reversed beside the real one."""
    out = io.StringIO()
    c = N.TerminalChannel(person="me", reader=_lines("x"), out=out, attempts=1, timeout_s=1.0)
    c.ask("pay Alice\u202e tsegid")
    assert "\u202e" not in out.getvalue()


class _Tty:
    """A stdin that says it is a terminal, over a pipe the test writes to."""

    closed = False

    def __init__(self, fd: int) -> None:
        self._fd = fd

    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return self._fd


@pytest.fixture
def tty(monkeypatch):
    r, w = os.pipe()
    monkeypatch.setattr("sys.stdin", _Tty(r))
    yield w
    os.close(r)
    os.close(w)


def test_the_reader_returns_a_whole_line(tty):
    os.write(tty, b"yes\n")
    assert N.stdin_reader(1.0) == "yes\n"


def test_a_line_with_no_newline_is_cut_at_the_timeout_not_waited_on(tty):
    os.write(tty, b"y")
    began = time.monotonic()
    assert N.stdin_reader(0.05) is None
    assert time.monotonic() - began < 5


def test_nothing_typed_is_cut_at_the_timeout(tty):
    assert N.stdin_reader(0.01) is None


def test_no_stdin_at_all_is_unavailable(monkeypatch):
    monkeypatch.setattr("sys.stdin", None)
    assert N.stdin_reader(0.01) is None


# -- harness bridge -----------------------------------------------------------------------------


def _bridge(read=lambda a: None) -> N.HarnessChannel:
    return N.HarnessChannel(payload=lambda a: {"questions": [{"question": a.text}]}, read=read)


def test_the_bridge_hands_the_payload_to_the_courier_while_nobody_has_answered():
    got = _bridge().relay(APPROVAL)
    assert got.status == N.PENDING and got.how == "harness"
    assert got.courier == {"questions": [{"question": "ship it?"}]}


def test_the_bridge_returns_what_the_reader_found():
    found = Answer(N.ANSWERED, N.APPROVED, who="harness:t", how="harness", recorded=True)
    assert _bridge(lambda a: found).relay(APPROVAL) is found


def test_the_bridge_puts_a_question_as_an_ask_user_question():
    got = _bridge().ask("which?", ("a", "b"))
    assert got.status == N.PENDING
    (q,) = got.courier["questions"]
    assert q["question"] == "which?" and [o["label"] for o in q["options"]] == ["a", "b"]


def test_the_bridge_cannot_push_and_says_so():
    done = _bridge().notify("x")
    assert done.status == N.UNAVAILABLE and not done.ok


# -- webhook family -----------------------------------------------------------------------------


def _http(status: int = 200) -> FakeHttpClient:
    h = FakeHttpClient()
    h.when("POST", URL, status=status, body=b"ok")
    return h


def _sent(http: FakeHttpClient) -> dict:
    return json.loads(http.calls[-1].body)


def _hook(cls=O.WebhookChannel, http=None, **kw):
    return cls(url=URL, http=http or _http(), timeout_s=3.0, **kw)


def test_a_webhook_posts_json_with_the_timeout_it_was_given():
    http = _http()
    assert _hook(http=http).notify("hello", N.WARNING).ok
    call = http.calls[-1]
    assert call.timeout == 3.0 and call.headers["Content-Type"] == "application/json"
    assert _sent(http) == {"event": "notify", "severity": "warning", "text": "hello"}


def test_a_generic_webhook_carries_the_approval_fields():
    http = _http()
    got = _hook(http=http).relay(Approval("Q1", "ship?", "abc", code="K3Y"))
    assert got.status == N.PENDING and got.how == "webhook"
    body = _sent(http)
    assert (body["event"], body["ask_id"], body["digest"], body["code"]) == (
        "approval", "Q1", "abc", "K3Y",
    )  # fmt: skip
    assert body["options"] == [N.APPROVED, N.DENIED]


def test_an_outbound_question_is_pending_never_answered():
    got = _hook().ask("go?", ("yes", "no"))
    assert got.status == N.PENDING and not got.text


def test_slack_gets_its_one_text_field():
    http = _http()
    _hook(O.SlackChannel, http).ask("go?", ("yes", "no"))
    body = _sent(http)
    assert list(body) == ["text"] and "go?" in body["text"] and "yes" in body["text"]


def test_teams_gets_an_adaptive_card():
    http = _http()
    _hook(O.TeamsChannel, http).notify("hello")
    body = _sent(http)
    assert body["type"] == "message"
    (card,) = body["attachments"]
    assert card["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert card["content"]["type"] == "AdaptiveCard"
    assert card["content"]["body"][0]["text"] == "hello"


def test_openclaw_gets_a_message_and_the_bearer_token():
    http = _http()
    _hook(O.OpenClawChannel, http, token="t0k").notify("hello")
    assert _sent(http) == {"message": "hello", "name": "ddflow"}
    assert http.calls[-1].headers["Authorization"] == "Bearer t0k"


def test_n8n_gets_the_generic_body():
    http = _http()
    _hook(O.N8nChannel, http).notify("hello")
    assert _sent(http)["event"] == "notify"


def test_no_token_sends_no_authorization_header():
    http = _http()
    _hook(http=http).notify("x")
    assert "Authorization" not in http.calls[-1].headers


def test_no_endpoint_is_unavailable_and_sends_nothing():
    http = FakeHttpClient()
    c = O.WebhookChannel(url="", http=http, timeout_s=1.0)
    assert c.notify("x").status == N.UNAVAILABLE
    assert c.ask("y").status == N.UNAVAILABLE
    assert not http.calls


@pytest.mark.parametrize("kind", ["timeout", "connection", "connect_timeout", "tls"])
def test_offline_is_unavailable_never_delivered(kind):
    http = FakeHttpClient()
    http.when("POST", URL, raises=kind)
    c = _hook(http=http)
    assert c.notify("x").status == N.UNAVAILABLE
    assert c.relay(APPROVAL).status == N.UNAVAILABLE


def test_a_refused_post_is_failed_and_keeps_the_url_out_of_the_detail():
    """A chat webhook's URL IS its secret: no detail may carry it."""
    secret = "https://hooks.example.test/services/T000/B000/s3cret"
    http = FakeHttpClient()
    http.when("POST", secret, status=403, body=b"no")
    done = O.SlackChannel(url=secret, http=http, timeout_s=1.0).notify("x")
    assert done.status == N.FAILED
    assert "s3cret" not in done.detail and "403" in done.detail


def test_a_refused_question_is_failed_like_a_refused_notification():
    http = FakeHttpClient()
    http.when("POST", URL, status=500, body=b"no")
    c = _hook(http=http)
    assert c.notify("x").status == N.FAILED
    got = c.ask("y")
    assert got.status == N.FAILED and "500" in got.detail
    assert c.relay(APPROVAL).status == N.FAILED


def test_a_bad_scheme_is_unavailable_and_keeps_the_url_out():
    c = O.WebhookChannel(url="file:///etc/passwd", http=FakeHttpClient(), timeout_s=1.0)
    done = c.notify("x")
    assert done.status == N.UNAVAILABLE and "passwd" not in done.detail


def test_the_scrub_runs_on_everything_that_leaves():
    http = _http()
    c = _hook(http=http, scrub=lambda s: s.replace("hunter2", "[X]"))
    c.ask("is hunter2 ok?", ("hunter2", "no"))
    assert "hunter2" not in http.calls[-1].body.decode()


def test_the_scrub_leaves_the_digest_and_code_alone():
    http = _http()
    c = _hook(http=http, scrub=lambda s: "[X]")
    c.relay(Approval("Q1", "text", "abc", code="K3Y"))
    body = _sent(http)
    assert (body["digest"], body["code"]) == ("abc", "K3Y")
    assert "text" not in body["text"] and "K3Y" in body["text"]


# -- desktop ------------------------------------------------------------------------------------


def _proc(returncode=0, error=None):
    calls = []

    def capture(argv, *, timeout, **kw):
        calls.append((argv, timeout))
        return SimpleNamespace(returncode=returncode, error=error, stdout="", stderr="")

    return SimpleNamespace(capture=capture, calls=calls)


def test_linux_uses_notify_send_with_the_text_after_a_separator():
    p = _proc()
    done = O.DesktopChannel(proc=p, platform="linux", timeout_s=2.0).notify("--help", N.CRITICAL)
    assert done.ok
    argv, timeout = p.calls[0]
    assert argv[0] == "notify-send" and "--urgency=critical" in argv
    assert argv[argv.index("--") + 1 :] == ["ddflow", "--help"] and timeout == 2.0


@pytest.mark.parametrize("text", ['x" & do shell script "id', '-edo shell script "id"'])
def test_macos_passes_the_text_as_an_argument_never_as_script_or_option(text):
    p = _proc()
    O.DesktopChannel(proc=p, platform="darwin", timeout_s=2.0).notify(text)
    argv = p.calls[0][0]
    assert argv[0] == "osascript" and argv[-3:] == ["--", "ddflow", text]
    assert not any(text in a for a in argv[:-1])


def test_the_desktop_text_is_scrubbed_like_every_other_word_that_leaves():
    p = _proc()
    c = O.DesktopChannel(proc=p, platform="linux", timeout_s=2.0, scrub=lambda s: "[X]")
    c.notify("hunter2")
    assert p.calls[0][0][-1] == "[X]"


def test_windows_is_unavailable_and_runs_nothing():
    p = _proc()
    done = O.DesktopChannel(proc=p, platform="win32", timeout_s=2.0).notify("x")
    assert done.status == N.UNAVAILABLE and not p.calls


def test_a_missing_binary_is_unavailable_a_nonzero_exit_failed():
    gone = _proc(returncode=None, error=FileNotFoundError("notify-send"))
    bad = _proc(returncode=1)
    assert (
        O.DesktopChannel(proc=gone, platform="linux", timeout_s=1).notify("x").status
        == N.UNAVAILABLE
    )
    assert O.DesktopChannel(proc=bad, platform="linux", timeout_s=1).notify("x").status == N.FAILED


def test_a_desktop_notification_cannot_take_an_answer():
    c = O.DesktopChannel(proc=_proc(), platform="linux", timeout_s=1)
    assert c.ask("go?", ("yes",)).status == N.UNAVAILABLE
    assert c.relay(APPROVAL).status == N.UNAVAILABLE


# -- email --------------------------------------------------------------------------------------


class _Smtp:
    """A stand-in `smtplib.SMTP`: records what happened, raises what it was told to, where."""

    def __init__(self, connect_error=None, tls_error=None):
        self.connect_error, self.tls_error = connect_error, tls_error
        self.calls, self.sent = [], []

    def __call__(self, host, port, timeout):
        self.calls.append(("connect", host, port, timeout))
        if self.connect_error is not None:
            raise self.connect_error
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.calls.append(("starttls",))
        if self.tls_error is not None:
            raise self.tls_error

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, msg):
        self.sent.append(msg)


def _mail(smtp, **kw):
    args = {"host": "mx.example.test", "port": 587, "sender": "a@example.test",
            "to": "b@example.test", "timeout_s": 4.0, "smtp": smtp}  # fmt: skip
    return O.EmailChannel(**{**args, **kw})


def test_email_sends_over_starttls_with_a_fixed_subject():
    s = _Smtp()
    assert _mail(s).notify("line one\nSubject: forged", N.WARNING).ok
    (msg,) = s.sent
    assert ("connect", "mx.example.test", 587, 4.0) in s.calls and ("starttls",) in s.calls
    assert msg["To"] == "b@example.test" and "\n" not in msg["Subject"]
    assert "forged" not in msg["Subject"] and "line one" in msg.get_content()


def test_email_logs_in_only_when_given_a_user():
    s = _Smtp()
    _mail(s).notify("x")
    assert not [c for c in s.calls if c[0] == "login"]
    _mail(s, user="u", password="p").notify("x")
    assert ("login", "u", "p") in s.calls


def test_email_with_tls_off_skips_starttls():
    s = _Smtp()
    _mail(s, tls=False).notify("x")
    assert ("starttls",) not in s.calls


def test_email_never_falls_back_to_plain_text():
    s = _Smtp(tls_error=smtplib.SMTPNotSupportedError("no STARTTLS"))
    assert _mail(s).notify("x").status == N.FAILED
    assert ("starttls",) in s.calls and not s.sent


def test_email_offline_is_unavailable_and_unconfigured_too():
    assert _mail(_Smtp(connect_error=OSError("no route"))).notify("x").status == N.UNAVAILABLE
    s = _Smtp()
    assert _mail(s, host="").notify("x").status == N.UNAVAILABLE
    assert _mail(s, to="").ask("y").status == N.UNAVAILABLE
    assert not s.calls


def test_a_malformed_address_is_failed_not_raised():
    assert _mail(_Smtp(), to="b@example.test\nBcc: c@example.test").notify("x").status == N.FAILED


def test_an_emailed_approval_carries_its_digest_and_stays_pending():
    s = _Smtp()
    got = _mail(s).relay(Approval("Q1", "ship?", "abc", code="K3Y"))
    assert got.status == N.PENDING and got.how == "email"
    body = s.sent[0].get_content()
    assert "Q1" in body and "abc" in body and "K3Y" in body


# -- the fake -----------------------------------------------------------------------------------


def test_the_fake_records_what_it_was_told():
    f = FakeHumanChannel(answers=["yes"])
    f.notify("n", N.CRITICAL)
    f.ask("q", ("yes",))
    f.relay(APPROVAL)
    assert f.calls == [("notify", "n", N.CRITICAL), ("ask", "q", ("yes",)), ("relay", APPROVAL)]


def test_the_fake_can_answer_as_a_scripted_status():
    f = FakeHumanChannel(answers=[Answer(N.PENDING, how="fake")])
    assert f.ask("q").status == N.PENDING
