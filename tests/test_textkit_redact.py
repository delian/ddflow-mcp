"""One Redactor with named profiles (B-uni-textkit.4, D-unify 6)."""

from __future__ import annotations

import socket

import pytest

from ddflow.config import Config
from ddflow.core import redact as R
from ddflow.services import bugreport, redact_report, sessions
from ddflow.services.export import safe

LEAKY = "key sk-abcdefghijklmnop1234 at 10.20.30.40 in /home/someone/proj mail a.b@example.org"


def test_the_old_homes_re_export_the_one_engine():
    assert redact_report.redact_report is R.redact_report
    assert bugreport.redact_argv is R.redact_argv
    assert safe.names_for is R.names_for
    assert redact_report.Redacted is R.Redacted


def test_an_unknown_profile_is_refused():
    with pytest.raises(ValueError, match="unknown redaction profile"):
        R.Redactor("nope")


@pytest.mark.parametrize("name", list(R.PROFILES))
def test_every_text_profile_removes_the_full_set_and_is_idempotent(name):
    red = R.Redactor(name, Config())
    out = red.text(LEAKY).text
    for leaked in ("sk-abcdefghijklmnop1234", "10.20.30.40", "/home/someone", "a.b@example.org"):
        assert leaked not in out, (name, leaked)
    assert red.text(out).text == out


def test_the_log_profile_keeps_the_words_around_a_secret():
    out = R.Redactor("log", Config()).text("password: hunter2hunter2 then go").text
    assert "password: [REDACTED]" in out and "then go" in out
    assert "[REDACTED:secret]" in R.Redactor("view", Config()).text("sk-abcdefghijklmnop1234").text


def test_log_and_view_do_not_read_the_machine(monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: "Zeta9.example.test")
    text = "ran on Zeta9 and zeta9.example.test"
    assert R.Redactor("log").text(text).text == text
    assert R.Redactor("view").text(text).text == text
    assert "Zeta9" not in R.Redactor("export").text(text).text
    assert "Zeta9" not in R.Redactor("upstream").text(text).text


def test_the_session_log_writes_through_the_log_profile():
    clean, n = sessions.redact(LEAKY, Config())
    assert clean == R.Redactor("log", Config()).text(LEAKY).text
    assert n == R.Redactor("log", Config()).text(LEAKY).total >= 4


def test_an_invalid_pattern_still_raises_with_the_json_remedy():
    cfg = Config()
    cfg.session.redact_patterns = ["(unclosed"]
    for name in R.PROFILES:
        with pytest.raises(ValueError, match="JSON"):
            R.Redactor(name, cfg).text("x")


def test_argv_shape_is_unchanged():
    got = R.redact_argv(["/usr/bin/ddflow", "bug", "--token", "x"], ["bug"], ["--token"])
    assert got == ["ddflow", "bug", "--token", "<value>"]
