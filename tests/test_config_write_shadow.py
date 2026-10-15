"""Bug B65bbc8083c: `config KEY VALUE` into a layer a higher layer shadows said nothing."""

from __future__ import annotations

import json

from conftest import run_cli

KEY = "schedule.max_parallel_tasks"


def test_writing_a_key_the_local_layer_shadows_names_the_winner(repo):
    assert run_cli(repo, "config", "--local", KEY, "6")[0] == 0
    code, out, err = run_cli(repo, "config", KEY, "7")
    assert code == 0, err  # a successful write stays exit 0
    text = out + err
    assert "7" in text and "shadow" in text
    assert "local" in text and "6" in text  # the winning layer and its value
    assert f"ddflow config --local {KEY} VALUE" in text  # the command that sets the winner
    assert (repo / ".ddflow" / "config.toml").read_text("utf-8").count("max_parallel_tasks = 7")


def test_the_json_body_carries_the_effective_value_and_layer(repo):
    run_cli(repo, "config", "--local", KEY, "6")
    code, out, _ = run_cli(repo, "--json", "config", KEY, "7")
    assert code == 0
    body = json.loads(out)
    assert body["effective"] == 6 and body["effective_source"] == "local"
    assert body["shadowed"] is True


def test_an_unshadowed_write_has_no_warning(repo):
    code, out, err = run_cli(repo, "config", KEY, "7")
    assert code == 0
    assert "shadow" not in out + err
