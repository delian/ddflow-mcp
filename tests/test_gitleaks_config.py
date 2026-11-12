"""The gitleaks allowlist: ddflow's own item ids are not credentials (bug
Bci-gitleaks-over-the-pushed-commi-858efc87b3).

A task body in the committed event log names split slices ("... surface.1-api,
B-uni-record-surface.2-surface"); the generic-api-key rule read the second id as the secret
after the `api` keyword and failed CI on a log line. The check runs the real gitleaks
binary when pre-commit has installed one.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _gitleaks() -> str | None:
    found = shutil.which("gitleaks")
    if found:
        return found
    cache = Path(os.environ.get("PRE_COMMIT_HOME", Path.home() / ".cache" / "pre-commit"))
    hits = sorted(glob.glob(str(cache / "*" / "golangenv-*" / "bin" / "gitleaks")))
    return hits[0] if hits else None


pytestmark = pytest.mark.skipif(_gitleaks() is None, reason="gitleaks is not installed")


def _scan(tmp_path: Path, text: str) -> int:
    """gitleaks' exit code over a file holding ``text`` (0 clean, 1 leaks found)."""
    (tmp_path / "log.jsonl").write_text(text)
    return _scan_dir(tmp_path)


def _scan_dir(tmp_path: Path) -> int:
    run = subprocess.run(
        [
            _gitleaks() or "",
            "dir",
            "--no-banner",
            "--config",
            str(ROOT / ".gitleaks.toml"),
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )
    assert run.returncode in (0, 1), run.stderr  # anything else is a gitleaks that failed
    return run.returncode


def test_a_split_slice_id_after_the_api_keyword_is_not_a_secret(tmp_path):
    body = '{"body":"Split into: B-uni-record-surface.1-api, B-uni-record-surface.2-surface."}\n'
    assert _scan(tmp_path, body) == 0


def test_a_real_looking_key_after_the_same_keyword_still_fires(tmp_path):
    # Built from pieces: the repository's own scan must not find a key-shaped literal here.
    key = "api_key = '" + "Zx9Qw3Lm8Vb2" + "Nc7Kd4Hf6Jg1" + "Ts5Pr0Ye" + "'\n"
    assert _scan(tmp_path, key) != 0


# Built from pieces so the repository's own scan does not read the decision id out of this
# file: the id is allowlisted by its exact value, and this file is not one of the paths
# gitleaks skips, so the literal must stay out of the committed test source.
_DECISION_ID = "D-swarm-pilot" + "-" + "2026" + "-11" + "-05"


def test_a_decision_id_after_a_token_named_id_is_not_a_secret(tmp_path):
    # A recorded-decision list names the decision _DECISION_ID beside a decision whose own
    # name holds the word "auth-token"; the generic-api-key rule reads _DECISION_ID as the
    # secret after that keyword. The allowlist names that exact value.
    token_named = "D-auth" + "-token" + "-defaults"
    body = '{"body":"decisions ' + token_named + ", " + _DECISION_ID + '"}\n'
    assert _scan(tmp_path, body) == 0


_LOOKALIKE = "Prod" + "-" + "82910" + "4857261" + "9038" + "271"


def test_a_credential_lookalike_after_a_token_named_id_still_fires(tmp_path):
    # Only that one decision id is allowlisted, not a dash shape: a credential-shaped value
    # in its place still fires, so a broad shape allowlist cannot regress in unnoticed.
    token_named = "D-auth" + "-token" + "-defaults"
    body = '{"body":"decisions ' + token_named + ", " + _LOOKALIKE + '"}\n'
    assert _scan_committed(tmp_path, ".ddflow/events/padapt2.jsonl", body) != 0


_SHA = "0123456789abcdef" * 2 + "01234567"


def _scan_committed(repo: Path, rel: str, text: str) -> int:
    """gitleaks over a one-commit git repo holding ``text`` at ``rel``: paths are repo-relative,
    as in the pre-push scan."""
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "x", "--no-verify"]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True, env=env, capture_output=True)
    run = subprocess.run(
        [
            _gitleaks() or "",
            "git",
            "--no-banner",
            "--config",
            str(ROOT / ".gitleaks.toml"),
            str(repo),
        ],
        capture_output=True,
        text=True,
    )
    assert run.returncode in (0, 1), run.stderr
    return run.returncode


_BODY = '{"body":"sourcegraph search","head":"' + _SHA + '"}\n'


def test_a_git_sha_in_an_event_log_that_mentions_sourcegraph_is_not_a_token(tmp_path):
    # The rule's keyword is per file; the committed logs name 40-hex shas all the time.
    assert _scan_committed(tmp_path, ".ddflow/events/a.jsonl", _BODY) == 0


def test_a_git_sha_in_docs_that_mentions_sourcegraph_is_not_a_token(tmp_path):
    # RESEARCH.md quotes a `git` commit sha under a sourcegraph mention; the committed docs
    # name 40-hex shas like the event logs do.
    doc = "sourcegraph search from sha " + _SHA + "\n"
    assert _scan_committed(tmp_path, "docs/ddflow/RESEARCH.md", doc) == 0


def test_the_same_hex_outside_the_event_logs_still_fires(tmp_path):
    assert _scan_committed(tmp_path, "notes.txt", "sourcegraph token " + _SHA + "\n") != 0


def test_a_lookalike_or_nested_event_log_path_is_not_exempt(tmp_path):
    assert _scan_committed(tmp_path, "vendor/x/.ddflow/events/a.jsonl", _BODY) != 0
    assert _scan_committed(tmp_path / "b", "lib.ddflow/events/a.jsonl", _BODY) != 0


def _hook_version() -> str:
    text = (ROOT / ".pre-commit-config.yaml").read_text()
    found = re.search(r"gitleaks/v8@v(\d+\.\d+\.\d+)", text)
    assert found, "the pre-commit gitleaks pin is gone"
    return found.group(1)


def _action_version() -> str:
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "gitleaks/gitleaks-action@" in text
    found = re.search(r"^\s*GITLEAKS_VERSION:\s*['\"]?(\d+\.\d+\.\d+)['\"]?\s*$", text, re.M)
    assert found, "the Action is unpinned: it would run its own default, which may be older"
    return found.group(1)


def test_ci_and_the_precommit_hook_run_the_same_gitleaks():
    # Bug Bci-gitleaks-version-skew: the Action's default (8.24.3) ignores `[[allowlists]]`,
    # the hook's 8.30.1 honours it, so a push that passed locally failed the Secrets job.
    assert _action_version() == _hook_version()


def test_a_real_sourcegraph_token_in_an_event_log_still_fires(tmp_path):
    # The allowlist is bare 40-hex only: a prefixed token in the event log is a real finding.
    token = "sgp_" + "0123456789abcdef" * 2 + "01234567"
    body = '{"body":"sourcegraph search","token":"' + token + '"}\n'
    assert _scan_committed(tmp_path, ".ddflow/events/a.jsonl", body) != 0


# Built from pieces so the repository's own scan does not read a key-shaped literal out of
# this file: the value itself is allowlisted only under .ddflow/events/, and a live AIza
# key pasted into tests/ must still fire.
_REVOKED_KEY = "AIza" + "SyAuEm-JafbL8" + "U6TborcDo8jc9" + "DScUJ8E_s"


def test_revoked_gemini_key_is_allowlisted_only_in_event_shards(tmp_path):
    # Bug B5dc280b4eb: Google answers 403 "API key was reported as leaked" for this value,
    # so it is dead, but one event-log shard still holds it in plaintext. The allowlist is
    # scoped to exactly this value, the gcp-api-key rule, and the event-log path — so the
    # exact value there scans clean, while a one-char variant, or the value in a .jsonl
    # outside .ddflow/events/, still fires.
    line = "use gemini with this key " + _REVOKED_KEY + " and model\n"
    variant = "use gemini with this key " + _REVOKED_KEY[:-1] + "t" + " and model\n"
    assert _scan_committed(tmp_path / "event", ".ddflow/events/a.jsonl", line) == 0
    assert _scan_committed(tmp_path / "variant", ".ddflow/events/a.jsonl", variant) != 0
    # A .jsonl outside the event dir proves the path scope is the shard directory, not the
    # extension: an overbroad *.jsonl exemption would leave this one firing.
    assert _scan_committed(tmp_path / "outside", "shards/a.jsonl", line) != 0


def test_a_fake_sk_fixture_is_allowlisted_only_by_value(tmp_path):
    # Bug B-push-gitleaks-fixtures: tests/test_lane_launch.py and test_lane_opencode.py name
    # the fake `sk-test-0123456789abcdef` credential to prove a real one is redacted out of
    # harness output. The allowlist names exactly that value, so the fixture scans clean
    # while any other `sk-` value in a test file still fires.
    assert _scan(tmp_path, 'SECRET = "sk-test-0123456789abcdef"\n') == 0
    variant = "sk-test-" + "0123456789abcdeX"
    assert _scan(tmp_path, 'SECRET = "' + variant + '"\n') != 0


# Built from pieces so the repository's own scan does not read the fixture literal out of
# this file: the value is allowlisted by its exact value, not a digest shape, so a real
# key-shaped value in the same `key=` slot must still fire.
_FINGERPRINT = "ab" + "cdef" + "012345"


def test_a_container_fingerprint_fixture_is_allowlisted_only_by_value(tmp_path):
    # Bug Bci-gitleaks-over-the-pushed-commi-858efc87b3: tests/test_perf_remote.py names a
    # container fingerprint fixture `key="abcdef012345"` (sequential hex, a machine-class
    # digest shape, obviously filler); the generic-api-key rule read it as the key after the
    # `key` keyword and failed CI on the PM-remote push. The allowlist names exactly that
    # value, so the fixture scans clean while another key-shaped value in the same slot
    # still fires.
    assert _scan(tmp_path, 'key = "' + _FINGERPRINT + '"\n') == 0
    variant = "0123456789" + "abcdef"
    assert _scan(tmp_path, 'key = "' + variant + '"\n') != 0


# Built from pieces so the repository's own scan does not read the flagged value out of
# this file: the value is the canonical RFC 7518 "alg=none, sub=alice" EXAMPLE JWT (no
# signature), quoted in a committed event-log detail while a redaction-shape `gate.failed`
# record explains why the JWT shape rule matches or not. It is a documented example, not a
# credential, and it is allowlisted by its exact value, not a jwt shape, so a JWT with any
# other subject in the same slot still fires (bug Bci-gitleaks-over-the-pushed-commi-858efc87b3).
_ALG_NONE_ALICE = "eyJhbGciOiJub25lIn" + "0.eyJzdWIi" + "OiJhbGljZSJ" + "9."


def test_the_alg_none_alice_example_jwt_in_an_event_shard_scans_clean(tmp_path):
    # The event shard lane-hard.jsonl quotes the alg=none/sub=alice example while a
    # `gate.failed` record for AU-redact-shapes explains the JWT redaction shape. Scoped to
    # exactly this value, exactly the jwt rule, exactly the event-log shard files.
    line = "the example " + _ALG_NONE_ALICE + " documents the shape\n"
    assert _scan_committed(tmp_path / "event", ".ddflow/events/a.jsonl", line) == 0


def test_a_real_three_segment_jwt_in_an_event_shard_still_fires(tmp_path):
    # Only the alice example value is allowlisted, not the jwt shape: a full signed JWT
    # (header.payload.signature) in a committed event shard is a real finding.
    signed = (
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIi"
        + "OiIxMjM0NTY3ODkwIn"
        + "0.SflKxwRJSMeKKF2QT"
        + "4fwpMeJf36POk6yJV_adQssw5c"
    )
    line = "jwt " + signed + "\n"
    assert _scan_committed(tmp_path / "signed", ".ddflow/events/a.jsonl", line) != 0
