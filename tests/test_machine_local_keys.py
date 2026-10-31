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


def _text(repo, local: bool) -> str:
    path = repo / ".ddflow" / ("local" if local else "") / "config.toml"
    return path.read_text("utf-8") if path.exists() else ""


def test_a_machine_local_key_defaults_to_the_local_layer(repo):
    code, out, err = run_cli(repo, "config", KEY, "7")
    assert code == 0, err
    assert "max_parallel_tasks = 7" in out and "not committed" in out
    assert "max_parallel_tasks = 7" in _text(repo, True)
    assert "max_parallel_tasks = 7" not in _text(repo, False)


def test_the_json_body_names_the_local_layer(repo):
    code, out, err = run_cli(repo, "--json", "config", KEY, "7")
    assert code == 0, err
    body = json.loads(out)
    assert body["local"] is True
    assert body["path"].replace("\\", "/").endswith(".ddflow/local/config.toml")


def test_a_plain_key_still_defaults_to_the_committed_layer(repo):
    code, _out, err = run_cli(repo, "config", "cadence.max_missed", "3")
    assert code == 0, err
    assert "max_missed = 3" in _text(repo, False)
    assert "max_missed = 3" not in _text(repo, True)


def test_the_enforce_knob_extends_the_classification(repo):
    # Without the knob lease.ttl_s defaults to the committed layer, as any plain key.
    code, _out, err = run_cli(repo, "config", "lease.ttl_s", "99")
    assert code == 0, err
    assert "ttl_s = 99" in _text(repo, False)
    # Classify it: now the same write defaults local, and stays out of the committed file.
    run_cli(repo, "config", "--set", "enforce.machine_local_keys", '["lease.ttl_s"]')
    code, _out, err = run_cli(repo, "config", "lease.ttl_s", "100")
    assert code == 0, err
    assert "ttl_s = 100" in _text(repo, True)
    assert "ttl_s = 100" not in _text(repo, False)


def test_an_appended_companion_block_defaults_to_the_local_layer(repo):
    # A kind='command' [[reviewer]] is refused by D-reviewer-trust, so the endpoint-table
    # half of the classification is covered with a [[companion]] block instead.
    doc = '[[companion]]\nname = "probe"\ncommand = "true"\n'
    code, out, err = run_cli(repo, "config", "--append-toml", doc)
    assert code == 0, err
    assert "local" in out
    assert 'name = "probe"' in _text(repo, True)
    assert 'name = "probe"' not in _text(repo, False)
