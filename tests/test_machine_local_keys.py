"""DX-machine-local-keys: machine-local keys default their `config KEY VALUE` writes to
the machine-local layer (bug B65bbc8083c follow-up). What ONE machine answers for itself
-- how many workers it runs, how it reaches its reviewers -- is not the next machine's
answer, and must not land in the committed file unless the operator deliberately sends it
there. `[enforce].machine_local_keys` extends the shipped classification.
"""

from __future__ import annotations

import json

from conftest import run_cli

KEY = "schedule.max_parallel_tasks"


def test_a_machine_local_key_defaults_to_the_local_layer(repo):
    code, out, err = run_cli(repo, "config", KEY, "7")
    assert code == 0, err
    assert "max_parallel_tasks = 7" in out and "not committed" in out
    assert "max_parallel_tasks = 7" in (repo / ".ddflow" / "local" / "config.toml").read_text(
        "utf-8"
    )
    assert "max_parallel_tasks" not in (repo / ".ddflow" / "config.toml").read_text("utf-8")


def test_the_json_body_names_the_local_layer(repo):
    code, out, err = run_cli(repo, "--json", "config", KEY, "7")
    assert code == 0, err
    body = json.loads(out)
    assert body["local"] is True
    assert body["path"].replace("\\", "/").endswith(".ddflow/local/config.toml")


def test_a_plain_key_still_defaults_to_the_committed_layer(repo):
    code, out, err = run_cli(repo, "config", "cadence.max_missed", "3")
    assert code == 0, err
    assert "max_missed = 3" in (repo / ".ddflow" / "config.toml").read_text("utf-8")
    assert "max_missed" not in (repo / ".ddflow" / "local" / "config.toml").read_text("utf-8")


def test_the_enforce_knob_extends_the_classification(repo):
    # lease.ttl_s is machine sizing but not in the shipped table: classify it here.
    run_cli(repo, "config", "--set", "enforce.machine_local_keys", '["lease.ttl_s"]')
    code, out, err = run_cli(repo, "config", "lease.ttl_s", "100")
    assert code == 0, err
    assert "ttl_s = 100" in (repo / ".ddflow" / "local" / "config.toml").read_text("utf-8")
    assert "ttl_s" not in (repo / ".ddflow" / "config.toml").read_text("utf-8")


def test_an_appended_reviewer_block_defaults_to_the_local_layer(repo):
    doc = '[[reviewer]]\nname = "x"\nkind = "command"\ncommand = "true"\n'
    code, out, err = run_cli(repo, "config", "--append-toml", doc)
    assert code == 0, err
    assert "local" in out
    assert "name = \"x\"" in (repo / ".ddflow" / "local" / "config.toml").read_text("utf-8")
    assert "name = \"x\"" not in (repo / ".ddflow" / "config.toml").read_text("utf-8")
