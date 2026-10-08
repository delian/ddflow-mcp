"""One Redactor with named profiles (B-uni-textkit.4, D-unify 6)."""

from __future__ import annotations

import socket

import pytest

from ddflow.config import Config
from ddflow.core import redact as R
from ddflow.services import bugreport, redact_report, sessions
from ddflow.services.export import safe

LAN = ".".join(("10", "20", "30", "40"))
LEAKY = f"key sk-abcdefghijklmnop1234 at {LAN} in /home/someone/proj mail a.b@example.org"


def test_the_old_homes_re_export_the_one_engine():
    assert redact_report.redact_text is R.redact_text
    assert bugreport.redact_argv is R.redact_argv
    assert safe.names_for is redact_report.names_for
    assert redact_report.Redacted is R.Redacted


def test_an_unknown_profile_is_refused():
    with pytest.raises(ValueError, match="unknown redaction profile"):
        redact_report.redactor("nope")


@pytest.mark.parametrize("name", list(R.PROFILES))
def test_every_text_profile_removes_the_full_set_and_is_idempotent(name):
    red = redact_report.redactor(name, Config())
    out = red.text(LEAKY).text
    for leaked in ("sk-abcdefghijklmnop1234", LAN, "/home/someone", "a.b@example.org"):
        assert leaked not in out, (name, leaked)
    assert red.text(out).text == out


def test_the_log_profile_keeps_the_words_around_a_secret():
    out = redact_report.redactor("log", Config()).text("password: hunter2hunter2 then go").text
    assert "password: [REDACTED]" in out and "then go" in out
    assert (
        "[REDACTED:secret]"
        in redact_report.redactor("view", Config()).text("sk-abcdefghijklmnop1234").text
    )


def test_view_never_reads_the_machine_but_log_export_upstream_do(monkeypatch, tmp_path):
    monkeypatch.setattr(socket, "gethostname", lambda: "Zeta9.example.test")
    monkeypatch.setenv("HOME", str(tmp_path / "hm"))
    text = "ran on Zeta9 and zeta9.example.test"
    assert redact_report.redactor("view").text(text).text == text
    for name in ("log", "export", "upstream"):
        out = redact_report.redactor(name).text(text).text
        assert "zeta9" not in out.lower(), name


def test_the_export_profile_keeps_the_repository_directory_name(monkeypatch, tmp_path):
    repo = tmp_path / "myproj"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.chdir(repo)
    out = redact_report.redactor("export").text("see myproj and /home/someone/myproj/x.py").text
    assert out == "see myproj and [REDACTED:path]"


def test_project_names_are_removed_by_every_text_profile():
    from types import SimpleNamespace

    cfg = Config()
    cfg.upstream = SimpleNamespace(redact_extra=["acme-internal"])
    for name in R.PROFILES:
        assert (
            "acme-internal"
            not in redact_report.redactor(name, cfg).text("deploy acme-internal now").text
        )


def test_the_session_log_writes_through_the_log_profile():
    clean, n = sessions.redact(LEAKY, Config())
    assert clean == redact_report.redactor("log", Config()).text(LEAKY).text
    assert n == redact_report.redactor("log", Config()).text(LEAKY).total >= 4


def test_an_invalid_pattern_still_raises_with_the_json_remedy():
    cfg = Config()
    cfg.session.redact_patterns = ["(unclosed"]
    for name in R.PROFILES:
        with pytest.raises(ValueError, match="JSON"):
            redact_report.redactor(name, cfg).text("x")


def test_argv_shape_is_unchanged():
    got = R.redact_argv(["/usr/bin/ddflow", "bug", "--token", "x"], ["bug"], ["--token"])
    assert got == ["ddflow", "bug", "--token", "<value>"]


def test_a_bare_padded_token_is_blanked_whole_not_kept_up_to_its_padding():
    cfg = Config()
    cfg.session.redact_extra = [r"[A-Za-z0-9+/]{16,}={1,2}"]
    out = redact_report.redactor("log", cfg).text("c2VjcmV0IGtleSBoZXJl==").text
    assert out == "[REDACTED]"


def test_the_full_hostname_is_redacted_before_its_short_label():
    out = redact_report.redactor("upstream", hostname="devbox.example.com", repo_root="").text(
        "on devbox.example.com"
    )
    assert out.text == "on [REDACTED:hostname]"


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("token=abc:def", "token= [REDACTED]"),
        ("token=abc=def", "token= [REDACTED]"),
        ("token: abc=def", "token: [REDACTED]"),
        ("Bearer abc:def", "Bearer [REDACTED]"),
    ],
)
def test_masking_splits_at_the_first_separator_so_no_value_prefix_survives(text, want):
    out, n = R.mask_secrets(text, [r"(?i)(token|bearer)\s*[:=]?\s*\S+"])
    assert (out, n) == (want, 1)


@pytest.mark.parametrize("name", list(R.PROFILES))
def test_a_secret_straddling_an_existing_marker_is_removed_whole(name):
    cfg = Config()
    cfg.session.redact_extra = [r"hunter\S+"]
    out = redact_report.redactor(name, cfg).text("pw hunter2[REDACTED:x]tail2 end").text
    assert "tail2" not in out
    assert "hunter" not in out


def test_masking_is_idempotent_with_a_straddling_marker_pattern():
    first = R.mask_secrets("api_key: abc123", [r"(?i)api_key\s*:\s*\S+"])[0]
    assert R.mask_secrets(first, [r"(?i)api_key\s*:\s*\S+"]) == (first, 0)


def test_redact_report_applies_the_configured_project_names_like_the_upstream_profile():
    cfg = Config()
    cfg.upstream = type("U", (), {"redact_extra": ["acme-internal"]})()
    text = "deploy acme-internal now"
    assert (
        redact_report.redact_report(text, cfg=cfg, repo_root="").text
        == redact_report.redactor("upstream", cfg, repo_root="").text(text).text
    )



def test_a_padded_base64_match_with_a_trailing_colon_is_blanked_whole():
    assert R.mask_secrets("cGFzc3dvcmQ=:", [r"\S+"]) == ("[REDACTED]", 1)
