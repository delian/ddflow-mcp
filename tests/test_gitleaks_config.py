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


def test_the_same_hex_outside_the_event_logs_still_fires(tmp_path):
    assert _scan_committed(tmp_path, "notes.txt", "sourcegraph token " + _SHA + "\n") != 0


def test_a_lookalike_or_nested_event_log_path_is_not_exempt(tmp_path):
    assert _scan_committed(tmp_path, "vendor/x/.ddflow/events/a.jsonl", _BODY) != 0
    assert _scan_committed(tmp_path / "b", "lib.ddflow/events/a.jsonl", _BODY) != 0
