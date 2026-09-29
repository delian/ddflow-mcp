"""The machine-local config layer: .ddflow/local/config.toml, never committed.

Operator, 2026-09-29: the critic, roborev and companion configurations stay local to
whoever runs the project; the repository and ddflow stay generic. Committed config is
generic project policy; what belongs to this machine -- its services, hosts, keys'
variable names, its sizing -- goes in the git-ignored local layer, which wins.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import Config
from ddflow.services import gates as G
from ddflow.services import review as R
from ddflow.services import sessions as S


def _write(root: Path, rel: str, text: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text)


def test_the_local_layer_wins_and_says_so(tmp_path):
    _write(tmp_path, ".ddflow/config.toml", "[lease]\nttl_s = 100\nheartbeat_s = 20\n")
    _write(tmp_path, ".ddflow/local/config.toml", "[lease]\nttl_s = 999\n")
    cfg = Config.load(tmp_path)
    assert cfg.lease.ttl_s == 999 and cfg.sources["lease.ttl_s"] == "local"
    assert cfg.lease.heartbeat_s == 20 and cfg.sources["lease.heartbeat_s"] == "file"


def test_a_local_reviewer_and_a_local_gate_command_are_used(tmp_path):
    _write(tmp_path, ".ddflow/config.toml", '[gate.unit_tests]\ncommand = "pytest -q"\n')
    _write(
        tmp_path,
        ".ddflow/local/config.toml",
        '[gate.unit_tests]\ncommand = "pytest -q -n 48"\n\n'
        '[[reviewer]]\nname = "mine"\nbase_url = "http://127.0.0.1:9/v1"\nmodel = "m"\n'
        'family = "alibaba"\ngates = ["critic"]\n',
    )
    _write(
        tmp_path, ".ddflow/local/reviewers.toml", '[[reviewer]]\nname = "also-mine"\nmodel = "x"\n'
    )
    assert G.load_gates(tmp_path, Config.load(tmp_path))["unit_tests"].command == "pytest -q -n 48"
    assert {r.name for r in R.load_reviewers(tmp_path)} == {"mine", "also-mine"}


def test_an_unknown_key_in_the_local_file_is_tolerated_like_the_committed_one(tmp_path):
    _write(tmp_path, ".ddflow/local/config.toml", "[lease]\nfrom_the_future = 1\n")
    assert Config.load(tmp_path).unknown_knobs == ["lease.from_the_future"]


def test_redact_extra_adds_to_the_built_in_patterns_instead_of_replacing_them():
    cfg = Config()
    cfg.session.redact_extra = [r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"]
    lan = ".".join(("10", "20", "30", "40"))
    clean, n = S.redact(f"critic at http://{lan}:8000 with api_key=sk-abcdefghijklmnop1234", cfg)
    assert lan not in clean, "the extra pattern applies"
    assert "sk-abcdefghijklmnop1234" not in clean, "the built-in secret patterns still apply"
    assert n >= 2


def test_init_keeps_the_local_layer_out_of_git(repo):
    run_cli(repo, "init")
    _write(repo, ".ddflow/local/config.toml", "[lease]\nttl_s = 1\n")
    r = subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", ".ddflow/local/config.toml"])
    assert r.returncode == 0, ".ddflow/local/config.toml would be committed"
