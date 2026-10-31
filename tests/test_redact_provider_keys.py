"""Provider credential shapes never reach the committed event log (Bsecbug-redact-google-api-key).

Every key here is synthetic and built at run time from parts, so the repository holds no
credential-shaped literal for a scanner to flag; each is obviously filler.
"""

from __future__ import annotations

import subprocess

import pytest

from ddflow.config import Config
from ddflow.core import redact_errors as RE
from ddflow.core.secret_shapes import SHAPES, shape_hit
from ddflow.infra.log import EventLog
from ddflow.services import enforce_secrets as ES
from ddflow.services import sessions as S

_F = "FAKEFAKE" * 8  # filler, plainly not a secret


def _fake(prefix: str, n: int) -> str:
    return prefix + (_F * 4)[:n]


KEYS = {
    "google": _fake("AI" + "za", 35),
    "anthropic": _fake("sk-" + "ant-api03-", 40),
    "openai": _fake("sk-" + "", 40),
    "openai-proj": _fake("sk-" + "proj-", 40),
    "xai": _fake("xai" + "-", 40),
    "groq": _fake("gsk" + "_", 40),
    "ghp": _fake("ghp" + "_", 36),
    "gho": _fake("gho" + "_", 36),
    "ghs": _fake("ghs" + "_", 36),
    "github-pat": _fake("github" + "_pat_", 40),
    "aws-akia": "AKIA" + "FAKEFAKEFAKEFAKE",
    "aws-asia": "ASIA" + "FAKEFAKEFAKEFAKE",
    "slack": _fake("xox" + "b-", 30),
    "stripe-sk": _fake("sk" + "_live_", 30),
    "stripe-rk": _fake("rk" + "_live_", 30),
    "hf": _fake("hf" + "_", 34),
}


@pytest.fixture
def cfg():
    return Config()


@pytest.mark.parametrize("name", sorted(KEYS))
def test_each_provider_shape_is_redacted_from_a_prompt(name, cfg):
    key = KEYS[name]
    clean, n = S.redact(f"please use {key} for the call", cfg)
    assert key not in clean
    assert "please use" in clean and n >= 1


def test_generic_assignment_with_a_long_mixed_value_is_redacted(cfg):
    value = "q9Zx7Lm2Pa4Rt6Vw8Yb0Cd1Ef3Gh"
    for text in (f'api_key = "{value}"', f'{{"client_secret": "{value}"}}', f"token: {value}"):
        clean, n = S.redact(text, cfg)
        assert value not in clean and n >= 1, text


def test_prompt_with_a_fake_key_never_reaches_the_shard_raw(tmp_path, cfg):
    log = EventLog(tmp_path)
    key = KEYS["google"]
    out = S.capture_prompt(log, cfg, "harness-1", f"here is my key {key} ok")
    assert out == "recorded"
    S.note(log, cfg, "s1", f"note {key}")
    S.prompt(log, cfg, "s1", f"typed {key}")
    raw = "".join(p.read_text() for p in log.shards())
    assert key not in raw
    prompts = [e for e in log.read_all() if e.kind == "session.prompt"]
    assert prompts and all(e.data["redactions"] >= 1 for e in prompts)


def test_the_writer_redacts_a_field_no_caller_redacted(tmp_path):
    log = EventLog(tmp_path)
    key = KEYS["anthropic"]
    log.append("session.note", "s1", {"text": f"raw {key}", "item": ""})
    assert key not in "".join(p.read_text() for p in log.shards())


def test_a_redaction_that_errors_fails_closed(tmp_path, cfg, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(S, "redactor", boom)
    key = KEYS["google"]
    clean, n = S.redact(f"key {key}", cfg)
    assert clean == RE.FAILED_MARKER and n == 1
    log = EventLog(tmp_path)
    S.capture_prompt(log, cfg, "h2", f"key {key}")
    raw = "".join(p.read_text() for p in log.shards())
    assert key not in raw and RE.FAILED_MARKER in raw


def test_the_event_writer_fails_closed_when_its_redactor_errors(tmp_path, monkeypatch):
    from ddflow.core import redact as R

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(R, "redact_event_data", boom)
    log = EventLog(tmp_path)
    key = KEYS["google"]
    log.append("session.note", "s1", {"text": f"x {key}", "item": ""})
    raw = "".join(p.read_text() for p in log.shards())
    assert key not in raw and RE.FAILED_MARKER in raw


def test_shapes_survive_a_project_that_replaces_redact_patterns(cfg):
    cfg.session.redact_patterns = []
    clean, _ = S.redact(KEYS["google"], cfg)
    assert KEYS["google"] not in clean


def test_the_shapes_knob_turns_them_off(cfg):
    cfg.session.redact_patterns = []
    cfg.log.redact_builtin_shapes = False
    assert KEYS["google"] in S.redact(KEYS["google"], cfg)[0]


@pytest.mark.parametrize(
    "text",
    [
        "a3f5c9e1b7d24680a3f5c9e1b7d24680a3f5c9e1",  # commit sha
        "dGhpcyBpcyBhIGJhc2U2NCBibG9iIHdpdGggbm90aGluZyBzZWNyZXQgaW4gaXQgYXQgYWxs" * 3,
        "123e4567-e89b-12d3-a456-426614174000",  # uuid
        "the AIza prefix is how Google keys start",
        "AI" + "za" + "short",
        "set the token to 5 and the secret to none",
        "hf_ is the Hugging Face prefix, sk- the OpenAI one",
        "a long word " + "x" * 5000,
    ],
)
def test_ordinary_text_is_not_redacted(text, cfg):
    clean, n = S.redact(text, cfg)
    assert clean == text and n == 0


def test_a_shape_inside_a_longer_word_is_left_alone(cfg):
    blob = "Zm9v" + KEYS["google"] + "bar"
    assert S.redact(blob, cfg)[1] == 0


def test_no_shape_is_slow_on_hostile_input():
    import time

    hostile = ("token=" * 3000) + ("a" * 100000) + ("AI" + "za" + "-" * 50000)
    t0 = time.perf_counter()
    for _name, rx in SHAPES:
        import re

        re.compile(rx).findall(hostile)
    assert time.perf_counter() - t0 < 3


# -- the commit check -------------------------------------------------------------


def _git(d, *a):
    subprocess.run(["git", "-C", str(d), *a], check=True, capture_output=True)


@pytest.fixture
def staged(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    shard = tmp_path / ".ddflow" / "events"
    shard.mkdir(parents=True)
    (shard / "a.jsonl").write_text('{"clean": 1}\n')
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path, shard / "a.jsonl"


def test_the_commit_check_blocks_a_shard_line_with_a_key_and_never_prints_it(staged):
    repo, shard = staged
    key = KEYS["google"]
    shard.write_text('{"clean": 1}\n{"text": "k ' + key + '"}\n')
    _git(repo, "add", "-A")
    code, msg = ES.check_event_secrets(repo, Config())
    assert code != 0
    assert "a.jsonl:2" in msg and "google-api-key" in msg
    assert key not in msg and key[4:20] not in msg


def test_the_commit_check_sees_a_json_escaped_key(staged):
    repo, shard = staged
    key = KEYS["google"]
    escaped = "".join(f"\\u{ord(c):04x}" for c in key)
    shard.write_text('{"clean": 1}\n{"text": "' + escaped + '"}\n')
    _git(repo, "add", "-A")
    assert ES.check_event_secrets(repo, Config()).exit != 0


def test_the_commit_check_passes_a_clean_shard_and_ignores_committed_lines(staged):
    repo, shard = staged
    shard.write_text('{"clean": 1}\n{"text": "nothing here"}\n')
    _git(repo, "add", "-A")
    assert ES.check_event_secrets(repo, Config()) == (0, "")


def test_an_already_committed_leak_does_not_block_later_commits(staged):
    repo, shard = staged
    shard.write_text('{"text": "' + KEYS["google"] + '"}\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "old leak")
    shard.write_text(shard.read_text() + '{"text": "fine"}\n')
    _git(repo, "add", "-A")
    assert ES.check_event_secrets(repo, Config()).exit == 0


def test_the_policy_knob_warns_or_turns_the_check_off(staged):
    repo, shard = staged
    shard.write_text(shard.read_text() + '{"text": "' + KEYS["xai"] + '"}\n')
    _git(repo, "add", "-A")
    cfg = Config()
    cfg.enforce.event_secrets = "warn"
    assert ES.check_event_secrets(repo, cfg).exit == 0
    assert "a.jsonl:2" in ES.check_event_secrets(repo, cfg).message
    cfg.enforce.event_secrets = "off"
    assert ES.check_event_secrets(repo, cfg) == (0, "")


def test_shape_hit_names_the_shape_not_the_value():
    assert shape_hit(KEYS["groq"]) == "groq"
    assert shape_hit("nothing") == ""


def test_the_commit_hook_entry_point_runs_the_secret_check(staged):
    from ddflow.services import enforce as E

    repo, shard = staged
    cfg = Config()
    cfg.enforce.commit_without_lease = "off"
    shard.write_text(shard.read_text() + '{"text": "' + KEYS["github-pat"] + '"}\n')
    _git(repo, "add", "-A")
    code, msg = E.check_commit(repo, cfg)
    assert code != 0 and "a.jsonl:2" in msg and KEYS["github-pat"] not in msg


def test_merging_in_a_line_the_other_parent_already_committed_is_not_refused(staged):
    repo, shard = staged
    _git(repo, "checkout", "-q", "-b", "other")
    shard.write_text(shard.read_text() + '{"text": "' + KEYS["google"] + '"}\n')
    _git(repo, "commit", "-qam", "old leak on other")
    _git(repo, "checkout", "-q", "-")
    (repo / "x.txt").write_text("x")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "diverge")
    subprocess.run(["git", "-C", str(repo), "merge", "--no-commit", "other"], capture_output=True)
    assert ES.check_event_secrets(repo, Config()).exit == 0
