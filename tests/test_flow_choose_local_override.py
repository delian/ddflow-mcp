"""Bug B025c8de942: `flow choose` claimed a choice in effect when the LOCAL config layer
overrode it.

`api/flow.py` judged `overridden = source in ("file", "env")`, while
`services/choices.overlay` applies a recorded choice only where the knob is still at its
default. A knob set in `.ddflow/local/config.toml` (source "local") is therefore NOT
overlaid -- yet the result said `in_effect=True`, the one thing it exists to get right.
"""

from __future__ import annotations

import json

from conftest import run_cli


def _set_locally(repo, knob: str, value: str) -> None:
    local = repo / ".ddflow" / "local"
    local.mkdir(parents=True, exist_ok=True)
    with (local / "config.toml").open("a") as fh:
        fh.write(f'\n[flow]\n{knob} = "{value}"\n')


def test_flow_choose_reports_a_local_override_as_not_in_effect(repo):
    run_cli(repo, "init")
    _set_locally(repo, "integration", "merge")
    code, out, err = run_cli(repo, "--json", "flow", "choose", "integration", "pr")
    assert code == 0, err
    res = json.loads(out)
    assert res["in_effect"] is False, f"a choice the local layer overrides claimed in effect: {res}"
    assert "local" in res["note"], res["note"]

    _code, out, _ = run_cli(repo, "--json", "flow", "show")
    row = next(r for r in json.loads(out)["choices"] if r["knob"] == "integration")
    assert (row["value"], row["source"]) == ("merge", "local"), "the local layer did not win"


def test_flow_show_names_the_local_override_too(repo):
    """`choices.report` carried the same `("file", "env")` predicate: `flow show` did not
    say a recorded choice was overridden when the local layer was what overrode it."""
    run_cli(repo, "init")
    run_cli(repo, "flow", "choose", "integration", "pr", "--reason", "x")
    _set_locally(repo, "integration", "merge")
    _code, out, _ = run_cli(repo, "--json", "flow", "show")
    row = next(r for r in json.loads(out)["choices"] if r["knob"] == "integration")
    assert row["source"] == "local"
    assert "overridden" in row, f"a recorded choice not in effect must say so: {row}"


def test_a_choice_with_no_config_override_is_still_in_effect(repo):
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "--json", "flow", "choose", "integration", "pr")
    assert code == 0 and json.loads(out)["in_effect"] is True, err
    # A second choice over a recorded one (source "log:...") is not an override either.
    code, out, err = run_cli(repo, "--json", "flow", "choose", "integration", "merge")
    assert code == 0 and json.loads(out)["in_effect"] is True, err


def test_flow_show_text_does_not_call_a_local_setting_undecided(repo):
    """The CLI renderer `_who` had a third copy of the predicate: a knob the local layer
    set rendered as "UNDECIDED -- the default applies at first use"."""
    run_cli(repo, "init")
    _set_locally(repo, "integration", "merge")
    _code, out, _ = run_cli(repo, "flow", "show")
    line = next(ln for ln in out.splitlines() if "integration" in ln)
    assert "UNDECIDED" not in line, line
    assert "local config" in line, line
