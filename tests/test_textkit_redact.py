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


def test_view_never_reads_the_machine_but_log_export_upstream_do(monkeypatch, tmp_path):
    monkeypatch.setattr(socket, "gethostname", lambda: "Zeta9.example.test")
    monkeypatch.setenv("HOME", str(tmp_path / "hm"))
    text = "ran on Zeta9 and zeta9.example.test"
    assert R.Redactor("view").text(text).text == text
    for name in ("log", "export", "upstream"):
        assert "Zeta9" not in R.Redactor(name).text(text).text, name


def test_the_export_profile_keeps_the_repository_directory_name(monkeypatch, tmp_path):
    repo = tmp_path / "myproj"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.chdir(repo)
    out = R.Redactor("export").text(f"see {repo}/x.py and myproj").text
    assert out.endswith("x.py and myproj") and "REDACTED:name" not in out


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


def test_a_bare_padded_token_is_blanked_whole_not_kept_up_to_its_padding():
    cfg = Config()
    cfg.session.redact_extra = [r"[A-Za-z0-9+/]{16,}={1,2}"]
    out = R.Redactor("log", cfg).text("c2VjcmV0IGtleSBoZXJl==").text
    assert out == "[REDACTED]"


def test_the_full_hostname_is_redacted_before_its_short_label():
    out = R.Redactor("upstream", hostname="devbox.example.com", repo_root="").text(
        "on devbox.example.com"
    )
    assert out.text == "on [REDACTED:hostname]"
