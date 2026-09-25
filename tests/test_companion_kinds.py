"""A companion is an MCP server or a command-line tool, and the two are not the same.

Every companion used to be an MCP server by assumption. `entry()` would build a launch
block for any `command`, `companions add` would write it into an agent's config, and the
agent would fail the JSON-RPC handshake the first time a gate reached for the tool — a
registration that reads as done and is not, which is the vacuous-pass class in config
form.

OptMem is the live example that forced the distinction: a genuinely useful memory tool,
recommended on purpose, and not an MCP server. `memo` has no `mcp` subcommand.

The three places the distinction has to hold are the three that a `registered`-shaped
test would silently get wrong for a tool that can never reach that state:

* `companions add` must REFUSE it, and say why;
* the report must not print an `add` line it knows will not work;
* gate coverage and the default-gap check must treat INSTALLED as the goal state,
  because `registered` is unreachable.

The last one is the one that hides. A `cli` companion would sit in the registry serving
`rules` forever, be installed, and never count toward that gate's coverage — so the
report would say the gate has nothing behind it while the tool it names sits on the
PATH. Same shape as the bug where `gaps` judged it by a state it cannot reach.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.services import companions as CO

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _companion(**over) -> CO.Companion:
    spec = {
        "id": "probe_target",
        "title": "Probe target",
        "detect": ["python3", "-c", "pass"],
        "install": "pip install nothing",
        "gates": ["rules"],
        "command": "probe_target",
    }
    spec.update(over)
    return CO.Companion(**spec)


# -- the type itself -------------------------------------------------------------------


def test_a_companion_is_an_mcp_server_unless_it_says_otherwise():
    """The default has to stay `mcp`, or every existing registry entry and every
    project's `.ddflow/companions.toml` silently stops being registrable."""
    assert _companion().kind == "mcp"
    assert _companion().is_mcp is True


def test_a_cli_companion_has_no_mcp_entry_and_refuses_to_invent_one():
    """`entry()` would happily produce a plausible-looking block from any `command`.
    Written into a config, it fails at the worst moment — mid-task, as an agent reaches
    for the tool a gate told it to use."""
    c = _companion(kind="cli")
    assert c.is_mcp is False
    with pytest.raises(ValueError) as e:
        c.entry()
    assert "not an MCP server" in str(e.value)
    # and it names the way out, rather than only the refusal
    assert "pip install nothing" in str(e.value)


def test_an_mcp_companion_still_builds_its_entry():
    e = _companion(command="x", args=["y"]).entry()
    assert e == {"command": "x", "args": ["y"]}


def test_an_unknown_kind_is_an_error_not_a_silent_non_server(tmp_path, monkeypatch):
    """`kind = "MCP"` is neither of the two values, so `is_mcp` is False and the
    companion quietly stops being registrable with no error anywhere. The loader
    rejects an unknown FIELD; it cannot know this field has a closed vocabulary."""
    repo = tmp_path / "r"
    (repo / ".ddflow").mkdir(parents=True)
    (repo / ".ddflow" / "companions.toml").write_text(
        '[[companion]]\nid = "typo"\nkind = "MCP"\ncommand = "x"\n'
    )
    with pytest.raises(ValueError) as e:
        CO.load(repo)
    assert "kind" in str(e.value) and "typo" in str(e.value)


# -- the three places it has to hold ---------------------------------------------------


def test_gate_coverage_counts_an_installed_cli_tool(tmp_path):
    """The one that hides.

    `registered` is a state a cli companion cannot reach. Judging coverage by it would
    report `rules` as having nothing behind it while the tool serving `rules` sits on
    the PATH — the report contradicting the line directly above it.
    """
    cli = CO.Status(_companion(id="optmem", kind="cli", gates=["rules"]), True, "", "")
    cover = CO.gate_coverage(tmp_path, [cli], ["rules", "implement"])
    assert cover["rules"] == ["optmem"], "an installed cli companion did not count"
    assert cover["implement"] == []


def test_gate_coverage_does_not_count_a_cli_tool_that_is_absent_or_unknown(tmp_path):
    """The other half, and the reason this is not just `if not is_mcp: count it`.

    A tool that is not there covers nothing, and one we could not probe is exactly as
    unknown as before we asked — counting either would be the unavailable-as-success
    class, which is the thing gate coverage exists to expose.
    """
    for installed in (False, None):
        st = CO.Status(_companion(id="optmem", kind="cli", gates=["rules"]), installed, "", "")
        cover = CO.gate_coverage(tmp_path, [st], ["rules"])
        assert cover["rules"] == [], f"a cli companion with installed={installed!r} counted"


def test_an_unregistered_mcp_server_still_does_not_count(tmp_path):
    """Unchanged behaviour, asserted so the fix above cannot quietly widen to servers:
    an MCP server that is installed but not registered is one command away from being
    usable and is NOT usable yet."""
    st = CO.Status(_companion(id="roborev", gates=["rules"]), True, "", "")
    assert CO.gate_coverage(tmp_path, [st], ["rules"])["rules"] == []


def test_companions_add_refuses_a_cli_tool_and_says_why(repo):
    """Explicitly asking for it is different from it being swept up by a default run:
    the operator asked for something specific, so answer the question."""
    run_cli(repo, "init")
    (repo / ".ddflow" / "companions.toml").write_text(
        '[[companion]]\nid = "clitool"\nkind = "cli"\ncommand = "true"\n'
        'detect = ["true"]\ninstall = "brew install clitool"\ndefault = true\n'
    )
    code, _out, err = run_cli(repo, "companions", "add", "--id", "clitool", "--force")
    assert code == REFUSED, f"expected a refusal, got {code}: {err}"
    assert "not an MCP server" in err
    assert "brew install clitool" in err, "refused without naming the way forward"


def test_a_default_add_does_not_sweep_up_a_cli_tool(repo):
    """With no `--id`, `add` takes every installed default. A cli tool among them would
    turn the whole call into a refusal and block the servers that CAN be registered."""
    run_cli(repo, "init")
    (repo / ".ddflow" / "companions.toml").write_text(
        '[[companion]]\nid = "clitool"\nkind = "cli"\ncommand = "true"\n'
        'detect = ["true"]\ninstall = "brew install clitool"\ndefault = true\n'
    )
    code, _out, err = run_cli(repo, "companions", "add")
    assert code != REFUSED, f"a cli companion blocked the default add: {err}"


def test_the_report_never_offers_to_register_a_cli_tool(repo):
    """An instruction the tool knows will fail is worse than no instruction: whoever
    follows it gets a config entry that breaks a gate later, far from here."""
    run_cli(repo, "init")
    (repo / ".ddflow" / "companions.toml").write_text(
        '[[companion]]\nid = "clitool"\nkind = "cli"\ncommand = "true"\n'
        'detect = ["true"]\ninstall = "brew install clitool"\ndefault = true\n'
    )
    _code, out, _err = run_cli(repo, "companions")
    assert "clitool" in out
    assert "companions add --id clitool" not in out


def test_the_json_surface_says_which_kind_each_one_is(repo):
    """An agent reading JSON has to be able to tell them apart too, or it writes the
    same broken config a human would have been warned off."""
    run_cli(repo, "init")
    code, out, _err = run_cli(repo, "--json", "companions")
    assert code in (OK, NOTHING)
    import json

    kinds = {c["id"]: c["kind"] for c in json.loads(out)["companions"]}
    assert kinds["optmem"] == "cli", kinds
    assert kinds["sequential"] == "mcp", kinds


# -- the registry entries this was built for -------------------------------------------


def test_the_shipped_registry_recommends_sequential_thinking_and_optmem(tmp_path):
    reg = {c.id: c for c in CO.load(tmp_path)}
    assert "sequential" in reg, "sequential-thinking is not in the shipped registry"
    assert reg["sequential"].kind == "mcp"
    assert reg["sequential"].default is True, "recommended means proposed by adopt"
    assert "optmem" in reg
    assert reg["optmem"].kind == "cli", "OptMem has no MCP mode; claiming one would lie"


def test_every_shipped_companion_declares_gates_that_exist(tmp_path):
    """A companion serving a gate nobody runs is a recommendation that buys nothing —
    the same inert-requirement shape `complete` already refuses for `[gates].required`.
    """
    from ddflow.config import Config

    cfg = Config.load(tmp_path)
    known = set(cfg.gates.task_pipeline) | set(cfg.gates.phase_pipeline)
    for c in CO.load(tmp_path):
        unknown = [g for g in c.gates if g not in known]
        assert not unknown, f"companion {c.id} serves unknown gate(s) {unknown}"


def test_every_shipped_companion_has_an_install_line_and_a_url(tmp_path):
    """The report prints both, and "ask the operator, then: " followed by nothing is
    how a recommendation becomes noise."""
    for c in CO.load(tmp_path):
        assert c.install.strip(), f"{c.id} has no install command"
        assert c.url.startswith("http"), f"{c.id} has no URL to read before installing"
