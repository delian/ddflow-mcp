"""A reviewer's API key from a file (`api_key_file`), beside `api_key_env`.

Every key here is a FAKE planted string; the tests grep every output for it."""

from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from ddflow.services import review as RV
from ddflow.services import review_keys as RK
from ddflow.services.lanes import endpoint as EP

FAKE = "sk-fake-PLANTED-9f3a7c1d2b"
ENV = "DDFLOW_TEST_KEY_FILE_VAR"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)


def _keyfile(tmp_path, mode=0o600, text=FAKE + "\n") -> Path:
    p = tmp_path / "k.key"
    p.write_text(text)
    p.chmod(mode)
    return p


def test_env_wins_over_file(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV, "from-env")
    got = RK.resolve_key(ENV, str(_keyfile(tmp_path)))
    assert got.value == "from-env" and got.source == "env"


def test_file_used_when_env_unset(tmp_path):
    got = RK.resolve_key(ENV, str(_keyfile(tmp_path)))
    assert got.value == FAKE and got.source == "file"


def test_tilde_is_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _keyfile(tmp_path)
    assert RK.resolve_key("", "~/k.key").value == FAKE


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660])
def test_group_or_world_accessible_file_is_refused(tmp_path, mode):
    got = RK.resolve_key(ENV, str(_keyfile(tmp_path, mode)))
    assert not got.value and "accessible by group or others" in got.reason
    assert FAKE not in got.reason and FAKE not in repr(got)


def test_symlink_to_open_file_is_refused(tmp_path):
    real = _keyfile(tmp_path, 0o644)
    link = tmp_path / "link"
    link.symlink_to(real)
    got = RK.resolve_key(ENV, str(link))
    assert not got.value and "refusing" in got.reason


def test_symlink_to_private_file_is_allowed(tmp_path):
    real = _keyfile(tmp_path)
    link = tmp_path / "link"
    link.symlink_to(real)
    assert RK.resolve_key(ENV, str(link)).value == FAKE


def test_file_owned_by_another_user_is_refused(tmp_path, monkeypatch):
    p = _keyfile(tmp_path)
    monkeypatch.setattr(os, "geteuid", lambda: os.stat(p).st_uid + 1)
    got = RK.resolve_key(ENV, str(p))
    assert not got.value and "not owned by the current user" in got.reason


@pytest.mark.parametrize("text", ["", "  \n", "two words\n", "a\nb\n"])
def test_empty_or_malformed_file_is_unavailable(tmp_path, text):
    got = RK.resolve_key(ENV, str(_keyfile(tmp_path, text=text)))
    assert not got.value and got.reason


def test_missing_and_unreadable_file_are_unavailable(tmp_path):
    assert "does not exist" in RK.resolve_key(ENV, str(tmp_path / "nope")).reason
    assert "not a regular file" in RK.resolve_key(ENV, str(tmp_path)).reason
    p = _keyfile(tmp_path, 0o000)
    got = RK.resolve_key(ENV, str(p))
    assert not got.value and got.reason


def test_not_cached(tmp_path):
    p = _keyfile(tmp_path)
    assert RK.resolve_key("", str(p)).value == FAKE
    p.write_text("rotated-key\n")
    assert RK.resolve_key("", str(p)).value == "rotated-key"


def test_scrub_removes_plain_and_quoted_key():
    key = "a/b+c=="
    out = RK.scrub(f"bad ?key=a%2Fb%2Bc%3D%3D and {key}", key)
    assert "a%2Fb" not in out and key not in out


def test_status_and_hints_name_but_never_show(tmp_path):
    rev = RV.Reviewer(name="r", api_key_env=ENV, api_key_file=str(_keyfile(tmp_path)))
    assert FAKE not in RK.status(ENV, rev.api_key_file)
    none = RV.Reviewer(name="n", api_key_env=ENV)
    assert RK.key_hints([rev, none]) == [f"reviewer n: {RK.status(ENV, '')}"]
    assert f"${ENV}" in RK.key_hints([none])[0] and "api_key_file" in RK.key_hints([none])[0]


# -- the three kinds ---------------------------------------------------------------------


class _Resp:
    def __init__(self, body):
        self._b = json.dumps(body).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _capture(monkeypatch, body):
    seen = {}

    def fake_open(req, timeout=0):
        seen["url"] = req.full_url
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        return _Resp(body)

    monkeypatch.setattr(RV, "_open", fake_open)
    return seen


def _rev(kind, tmp_path, **kw):
    return RV.Reviewer(
        name="r",
        kind=kind,
        base_url="https://example.invalid/v1",
        model="m",
        api_key_env=ENV,
        api_key_file=str(_keyfile(tmp_path)),
        **kw,
    )


def test_openai_reads_the_file(tmp_path, monkeypatch):
    seen = _capture(monkeypatch, {"choices": [{"message": {"content": "STATUS: NO FINDINGS"}}]})
    _text, err = RV._chat_openai(_rev("openai", tmp_path), "s", "u", 5)
    assert not err and seen["headers"]["authorization"] == f"Bearer {FAKE}"


def test_anthropic_reads_the_file(tmp_path, monkeypatch):
    seen = _capture(monkeypatch, {"content": [{"type": "text", "text": "STATUS: NO FINDINGS"}]})
    _text, err = RV._chat_anthropic(_rev("anthropic", tmp_path), "s", "u", 5)
    assert not err and seen["headers"]["x-api-key"] == FAKE


def test_gemini_reads_the_file(tmp_path, monkeypatch):
    body = {"candidates": [{"content": {"parts": [{"text": "STATUS: NO FINDINGS"}]}}]}
    seen = _capture(monkeypatch, body)
    _text, err = RV._chat_gemini(_rev("gemini", tmp_path), "s", "u", 5)
    assert not err and seen["headers"]["x-goog-api-key"] == FAKE
    assert FAKE not in seen["url"] and "key=" not in seen["url"]


@pytest.mark.parametrize("kind", ["openai", "anthropic", "gemini"])
def test_unreadable_key_file_is_unavailable_for_every_kind(kind, tmp_path, monkeypatch):
    _capture(monkeypatch, {})
    rev = _rev(kind, tmp_path)
    Path(rev.api_key_file).chmod(0o644)
    res = RV.review(rev, "diff --git a/x b/x\n+x\n", intent="i")
    assert res.status == RV.UNAVAILABLE
    blob = json.dumps(res.to_dict() if hasattr(res, "to_dict") else repr(res), default=str)
    assert FAKE not in blob and "refusing" in blob


@pytest.mark.parametrize("kind", ["openai", "anthropic", "gemini"])
def test_a_server_echoing_the_key_does_not_leak_it(kind, tmp_path, monkeypatch):
    import urllib.error

    def boom(req, timeout=0):
        raise urllib.error.HTTPError(
            req.full_url, 401, "no", {}, io.BytesIO(f"bad key {FAKE}".encode())
        )

    monkeypatch.setattr(RV, "_open", boom)
    fn = {"openai": RV._chat_openai, "anthropic": RV._chat_anthropic, "gemini": RV._chat_gemini}[
        kind
    ]
    _text, err = fn(_rev(kind, tmp_path), "s", "u", 5)
    assert err.startswith("HTTP 401") and FAKE not in err


def test_nothing_printed_holds_the_key(tmp_path, monkeypatch):
    out, errs = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(errs):
        got = RK.resolve_key(ENV, str(_keyfile(tmp_path)))
        print(
            got, RK.status(ENV, str(tmp_path / "k.key")), RK.key_hints([_rev("openai", tmp_path)])
        )
        print(repr(_rev("openai", tmp_path).key_result()))
    assert FAKE not in out.getvalue() + errs.getvalue()


# -- configuration -----------------------------------------------------------------------


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


BLOCK = '[[reviewer]]\nname = "g"\nkind = "gemini"\nmodel = "m"\napi_key_file = "~/k.key"\n'


def test_local_reviewers_file_accepts_api_key_file(tmp_path):
    _write(tmp_path, ".ddflow/local/reviewers.toml", BLOCK)
    assert RV.load_reviewers(tmp_path)[0].api_key_file == "~/k.key"


@pytest.mark.parametrize("rel", [".ddflow/config.toml", ".ddflow/reviewers.toml"])
def test_committed_config_rejects_api_key_file(tmp_path, rel):
    _write(tmp_path, rel, BLOCK)
    with pytest.raises(ValueError, match="machine-local"):
        RV.load_reviewers(tmp_path)


# -- lanes -------------------------------------------------------------------------------


def test_lane_endpoint_uses_the_same_helper(tmp_path, monkeypatch):
    ep = EP.Endpoint("r", "http://h", "m", ENV, api_key_file=str(_keyfile(tmp_path)))
    assert EP.resolve_key(ep).value == FAKE
    assert FAKE not in repr(ep) and str(tmp_path) not in repr(ep)
    assert EP.launch_env(ep) == {ENV: ENV}  # names only; the value is never in a config


def test_key_result_is_not_serialisable_to_a_dict(tmp_path):
    import dataclasses
    import pickle

    got = RK.resolve_key("", str(_keyfile(tmp_path)))
    assert not dataclasses.is_dataclass(got) and FAKE not in repr(got) + str(got)
    with pytest.raises(TypeError):
        pickle.dumps(got)


def test_a_nul_byte_or_control_character_is_not_a_key(tmp_path):
    got = RK.resolve_key("", str(_keyfile(tmp_path, text="ab\x00cd")))
    assert not got.value and "printable" in got.reason


def test_default_env_name_is_never_read_unless_configured(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "ambient")
    rev = RV.Reviewer(name="g", kind="gemini")
    got = rev.key_result("GEMINI_API_KEY")
    assert not got.value and "GEMINI_API_KEY" in got.reason


def test_an_env_key_with_a_control_character_is_refused_without_echo(monkeypatch):
    monkeypatch.setenv(ENV, "sk-\rX: " + FAKE)
    got = RK.resolve_key(ENV, "")
    assert not got.value and FAKE not in got.reason and ENV in got.reason
