"""Lane workers start on the core tool tier (LS-worker-mcp-config, P-lean-start).

A worker that loads every ddflow schema pays ~27K more than one on the `core` tier (measured
84.2K against 54.7K, docs/ddflow/research/lean-start.md lever 2), and the tier is chosen by an
environment variable that already exists. `adopt` writes a worker MCP config beside the project's
own, in the git-ignored local layer, and the doctor says when a host would send every schema.
Nothing is removed: every tool stays callable by name on any tier.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import MCP_TOOL_TIERS, Config
from ddflow.services import workermcp as WM
from ddflow.surfaces.mcp import TOOLS, Server
from ddflow.surfaces.tools.tiers import tier_tools

REL = WM.REL


@pytest.fixture(autouse=True)
def _hermetic_tier_env(monkeypatch):
    """The tiers come from knobs; an inherited override must not decide a test."""
    for name in ("DDFLOW_MCP_TOOLS", "DDFLOW_MCP_WORKER_TOOLS", "ENABLE_TOOL_SEARCH"):
        monkeypatch.delenv(name, raising=False)


def test_the_knob_defaults_to_core_and_takes_only_known_tiers(repo):
    cfg = Config.load(repo)
    assert cfg.mcp.worker_tools == "core"
    assert "core" in MCP_TOOL_TIERS


def test_the_worker_entry_adds_the_tier_and_keeps_the_rest():
    entry = {"command": "uvx", "args": ["ddflow-mcp"], "env": {"PYTHONPATH": "/x"}}
    got = WM.worker_entry(entry, "core")
    assert got["env"] == {"PYTHONPATH": "/x", "DDFLOW_MCP_TOOLS": "core"}
    assert got["command"] == "uvx" and got["args"] == ["ddflow-mcp"]
    assert "DDFLOW_MCP_TOOLS" not in entry.get("env", {}), "the input must not be changed"
    assert WM.worker_entry({"command": "c"}, "standard")["env"] == {"DDFLOW_MCP_TOOLS": "standard"}


def test_a_docker_launch_forwards_the_tier_into_the_container():
    entry = {"command": "docker", "args": ["run", "-i", "--rm", "img"]}
    got = WM.worker_entry(entry, "core")
    assert got["args"] == ["run", "-e", "DDFLOW_MCP_TOOLS=core", "-i", "--rm", "img"], got
    assert entry["args"] == ["run", "-i", "--rm", "img"], "the input must not be changed"
    assert "env" not in got, "the client-side env would be inert; the -e flag carries the tier"
    assert WM.worker_entry({"command": "uvx", "args": ["x"]}, "core")["args"] == ["x"]
    # not a `docker run`: left alone
    exec_entry = {"command": "docker", "args": ["exec", "worker", "run"]}
    assert WM.worker_entry(exec_entry, "core")["args"] == ["exec", "worker", "run"]
    # a tier already forwarded is replaced, not shadowed (the last -e would win)
    had = {"command": "docker", "args": ["run", "-e", "DDFLOW_MCP_TOOLS=all", "-e", "K=1", "img"]}
    assert WM.worker_entry(had, "core")["args"] == [
        "run",
        "-e",
        "DDFLOW_MCP_TOOLS=core",
        "-e",
        "K=1",
        "img",
    ]
    # arguments after the image belong to the container command, not to docker
    cmd = {"command": "docker", "args": ["run", "img"]}
    assert WM.worker_entry(cmd, "core")["args"] == ["run", "-e", "DDFLOW_MCP_TOOLS=core", "img"]
    again = WM.worker_entry(WM.worker_entry(had, "core"), "standard")
    assert again["args"].count("DDFLOW_MCP_TOOLS=standard") == 1
    assert not any("=core" in a for a in again["args"])


def test_adopt_for_claude_writes_the_worker_config_and_says_how_to_use_it(repo):
    code, out, err = run_cli(repo, "adopt", "--agents", "claude")
    assert code == 0, err
    data = json.loads((repo / REL).read_text())
    assert data["mcpServers"]["ddflow"]["env"]["DDFLOW_MCP_TOOLS"] == "core"
    assert f"--mcp-config={REL}" in out and "--strict-mcp-config" in out, out
    code, out, _ = run_cli(repo, "adopt", "--agents", "claude")
    assert code == 0 and f"{REL} is current" in out, out
    assert "--strict-mcp-config" not in out, "the hint is said when the file is written"


def test_the_knob_picks_the_tier_in_the_file(repo):
    run_cli(repo, "init")
    assert run_cli(repo, "config", "--set", "mcp.worker_tools", "standard")[0] == 0
    run_cli(repo, "adopt", "--agents", "claude")
    env = json.loads((repo / REL).read_text())["mcpServers"]["ddflow"]["env"]
    assert env["DDFLOW_MCP_TOOLS"] == "standard"


def test_the_file_is_in_the_git_ignored_local_layer(repo):
    run_cli(repo, "adopt", "--agents", "claude")
    import subprocess

    ignored = subprocess.run(
        ["git", "-C", str(repo), "check-ignore", "-q", REL], capture_output=True
    )
    assert ignored.returncode == 0, "the worker config holds this machine's launcher path"


def test_another_agent_gets_no_worker_config(repo):
    code, _out, err = run_cli(repo, "adopt", "--agents", "cursor")
    assert code == 0, err
    assert not (repo / REL).exists()


def test_a_server_started_with_that_env_lists_only_the_core_tier(repo, monkeypatch):
    run_cli(repo, "adopt", "--agents", "claude")
    env = json.loads((repo / REL).read_text())["mcpServers"]["ddflow"]["env"]
    monkeypatch.setenv("DDFLOW_MCP_TOOLS", env["DDFLOW_MCP_TOOLS"])
    listed = Server(repo).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in listed["result"]["tools"]}
    assert names == set(tier_tools("core")) and len(names) < len(TOOLS)


def test_a_tool_outside_the_core_tier_is_still_callable_by_name(repo, monkeypatch):
    monkeypatch.setenv("DDFLOW_MCP_TOOLS", "core")
    hidden = "ddflow_lanes"
    assert hidden in TOOLS and hidden not in tier_tools("core")
    srv = Server(repo)
    got = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": hidden, "arguments": {}},
        }
    )
    assert "error" not in got and not got["result"].get("isError"), got


def test_a_docker_entry_ending_in_an_option_is_left_alone():
    odd = {"command": "docker", "args": ["run", "img", "--flag"]}
    got = WM.worker_entry(odd, "core")
    assert got["args"] == ["run", "img", "--flag"] and "env" not in got, got


def test_the_recipe_names_the_flags_and_the_deferral_switch():
    text = WM.recipe(REL)
    for must in ("--mcp-config=", "--strict-mcp-config", "ENABLE_TOOL_SEARCH"):
        assert must in text, must


def test_only_the_documented_values_count_as_no_deferral():
    for odd in ("0", "off", "no", "true"):
        assert WM.deferral_note({"ENABLE_TOOL_SEARCH": odd}, tier="all") == "", odd


def test_the_doctor_note_says_when_a_host_would_send_every_schema():
    off = WM.deferral_note({"ENABLE_TOOL_SEARCH": "false"}, tier="all")
    assert off and "every ddflow schema" in off and "core" in off
    assert WM.deferral_note({"ENABLE_TOOL_SEARCH": "false"}, tier="core") == ""
    assert WM.deferral_note({"ENABLE_TOOL_SEARCH": "false"}, tier="standard") == ""
    assert WM.deferral_note({}, tier="all") == "", "default hosts defer; nothing to say"
    auto = WM.deferral_note({"ENABLE_TOOL_SEARCH": "auto:90"}, tier="all")
    assert auto, "auto:90 sets the threshold above ddflow's size: nothing deferred (measured)"


def test_doctor_judges_the_workers_tier_not_the_lanes_own(repo, monkeypatch):
    run_cli(repo, "init")
    monkeypatch.delenv("DDFLOW_MCP_TOOLS", raising=False)
    monkeypatch.delenv("DDFLOW_MCP_WORKER_TOOLS", raising=False)
    monkeypatch.setenv("ENABLE_TOOL_SEARCH", "false")

    def notes() -> list[str]:
        return json.loads(run_cli(repo, "doctor", "--json")[1])["notes"]

    assert not any("every ddflow schema" in n for n in notes()), "workers default to core"
    run_cli(repo, "config", "--set", "mcp.worker_tools", "all")
    assert any("every ddflow schema" in n for n in notes())
    run_cli(repo, "config", "--set", "mcp.tools", "core")  # the lane's own tier does not matter
    assert any("every ddflow schema" in n for n in notes())
    monkeypatch.delenv("ENABLE_TOOL_SEARCH")
    assert not any("every ddflow schema" in n for n in notes())


def test_the_recipe_names_the_tier_the_file_carries(repo, monkeypatch):
    monkeypatch.delenv("DDFLOW_MCP_WORKER_TOOLS", raising=False)
    assert "only the `core` tier keeps that small" in WM.recipe(tier="all")
    assert "`standard` tier" in WM.recipe(tier="standard")
    run_cli(repo, "init")
    run_cli(repo, "config", "--set", "mcp.worker_tools", "standard")
    _code, out, _ = run_cli(repo, "adopt", "--agents", "claude")
    assert "`standard` tier" in out and "starts ddflow on the `core` tier" not in out


def test_the_unreleased_knob_fragment_loads_with_a_json_default():
    """`new` is JSON-encoded in a manifest fragment; a bare `core` made every `ddflow upgrade` fail."""
    from ddflow.services import upgrade_manifest as UM

    manifest = UM.load()  # raises ManifestError on a fragment it cannot read
    change = next(
        c for _v, _d, changes in manifest.releases for c in changes if c.key == "mcp.worker_tools"
    )
    assert change.new == "core"


def test_the_recipe_gives_a_role_definition_the_worker_config_with_the_measured_numbers():
    """Measured with the role definitions allow-listing both spellings (lean-start-inherit.md section 2)."""
    text = WM.recipe(REL)
    assert f"--agent ddflow-<role> --strict-mcp-config --mcp-config={REL}" in text
    assert f"--strict-mcp-config --mcp-config={REL}` starts ddflow" in text
    for number in ("10.7K", "11.9K", "17.8K", "25.9K"):
        assert number in text, number


def test_the_flag_is_given_with_an_equals_sign_so_it_cannot_swallow_the_prompt():
    for text in (
        WM.recipe(REL),
        (Path(__file__).resolve().parents[1] / "ddflow/templates/prompts/help/mcp.md").read_text(),
    ):
        assert "--mcp-config " not in text, (
            "variadic: a space lets it read the prompt as a config path"
        )


def test_the_help_page_pairs_each_command_with_its_own_measured_number():
    page = (
        Path(__file__).resolve().parents[1] / "ddflow/templates/prompts/help/mcp.md"
    ).read_text()
    lines = page.splitlines()
    role = next(ln for ln in lines if "--agent ddflow-<role>" in ln and "claude " in ln)
    worker = next(ln for ln in lines if ln.strip().startswith("claude --strict-mcp-config"))
    assert f"--mcp-config={REL}" in role and "10.7K to 17.8K" in role, role
    assert f"--mcp-config={REL}" in worker and "25.9K" in worker and "17.8K" not in worker, worker


def test_the_recipe_does_not_call_the_configured_tier_core_when_it_is_not():
    text = WM.recipe(REL, tier="all")
    assert "starts ddflow on the `all` tier" in text
    assert "measured 10.7K for the researcher" in text and "on the core tier" in text, (
        "the numbers say so"
    )


def test_the_recipe_and_the_help_page_agree_on_the_commands_and_the_numbers():
    import re

    page = (
        Path(__file__).resolve().parents[1] / "ddflow/templates/prompts/help/mcp.md"
    ).read_text()
    cmd = re.compile(r"claude (?:--agent \S+ )?--strict-mcp-config --mcp-config=\S+?(?=[`\s]|$)")
    in_recipe = set(cmd.findall(WM.recipe(REL)))
    in_page = set(cmd.findall(page))
    assert in_recipe and in_recipe == in_page, (in_recipe, in_page)
    for number in ("10.7K", "11.9K", "17.8K", "25.9K"):
        assert number in WM.recipe(REL) and number in page, number


# --- LS-worker-lane-override: a lane picks its own tier ---------------------------------------


def test_the_lane_knob_defaults_to_no_override(repo):
    assert Config.load(repo).mcp.lane_tools == ""


def test_parse_lane_tiers_reads_lane_equals_tier_pairs():
    assert WM.parse_lane_tiers("") == {}
    assert WM.parse_lane_tiers("a=standard, b=all") == {"a": "standard", "b": "all"}


@pytest.mark.parametrize(
    "bad", ["a", "a=nope", "=core", "a/b=core", "../x=core", "a=core,a=all", "a=core,A=all"]
)
def test_parse_lane_tiers_refuses_bad_entries(bad):
    with pytest.raises(ValueError):
        WM.parse_lane_tiers(bad)


def test_the_lane_file_name_is_beside_the_default_one():
    assert WM.lane_rel("impl") == ".ddflow/local/worker-mcp-impl.json"


def test_adopt_writes_one_file_per_lane_with_its_tier(repo):
    run_cli(repo, "init")
    assert run_cli(repo, "config", "--set", "mcp.lane_tools", "impl=standard,rev=all")[0] == 0
    code, out, err = run_cli(repo, "adopt", "--agents", "claude")
    assert code == 0, err
    for lane, tier in (("impl", "standard"), ("rev", "all")):
        data = json.loads((repo / WM.lane_rel(lane)).read_text())
        assert data["mcpServers"]["ddflow"]["env"]["DDFLOW_MCP_TOOLS"] == tier
        assert WM.lane_rel(lane) in out
    default = json.loads((repo / REL).read_text())
    assert default["mcpServers"]["ddflow"]["env"]["DDFLOW_MCP_TOOLS"] == "core"


def test_a_lane_file_that_is_current_is_not_rewritten_and_a_dropped_lane_is_reported(repo):
    run_cli(repo, "init")
    run_cli(repo, "config", "--set", "mcp.lane_tools", "impl=all")
    run_cli(repo, "adopt", "--agents", "claude")
    _c, out, _ = run_cli(repo, "adopt", "--agents", "claude")
    assert f"{WM.lane_rel('impl')} is current" in out
    run_cli(repo, "config", "--set", "mcp.lane_tools", "")
    _c, out, _ = run_cli(repo, "adopt", "--agents", "claude")
    assert WM.lane_rel("impl") in out and "no longer in" in out  # stale file named, not deleted
    assert (repo / WM.lane_rel("impl")).is_file()


def test_the_knob_rejects_a_bad_value(repo):
    run_cli(repo, "init")
    code, _o, _e = run_cli(repo, "config", "--set", "mcp.lane_tools", "impl=bogus")
    assert code != 0


def test_the_knob_has_an_upgrade_fragment():
    from ddflow.services import upgrade_manifest as UM

    manifest = UM.load()
    got = [c for _v, _d, changes in manifest.releases for c in changes if c.key == "mcp.lane_tools"]
    assert got and got[0].new == ""  # JSON-encoded in the fragment: '""' is the empty string


def _stale(repo, spec):
    return [x for x in WM.write_lanes(repo, {"command": "c"}, spec) if "no longer" in x]


def _ignores_case(repo):
    return (repo / WM.lane_rel("impl")).exists() and (repo / WM.lane_rel("IMPL")).exists()


def test_a_lane_a_filesystem_lists_under_another_spelling_is_not_stale(tmp_path, monkeypatch):
    """A filesystem that ignores case lists `worker-mcp-IMPL.json` for the lane `impl` and no
    `worker-mcp-impl.json` beside it: writing nothing gives that same listing on any filesystem."""
    (tmp_path / WM.lane_rel("IMPL")).parent.mkdir(parents=True)
    (tmp_path / WM.lane_rel("IMPL")).write_text("{}")
    monkeypatch.setattr(WM, "_write_file", lambda *_a: "wrote")
    assert _stale(tmp_path, "impl=all") == []


@pytest.mark.parametrize(
    "make",
    [
        lambda path, _lane: path.write_text("{}"),  # a second file, keeping its old tier
        lambda path, lane: os.link(lane, path),
        lambda path, _lane: path.symlink_to(path.parent / "missing.json"),
    ],
    ids=["file", "hard-link", "dangling-symlink"],
)
def test_a_second_directory_entry_differing_only_in_case_is_reported(tmp_path, make):
    WM.write_lanes(tmp_path, {"command": "c"}, "impl=all")
    if _ignores_case(tmp_path):
        pytest.skip("this filesystem ignores case: there is no second entry")
    make(tmp_path / WM.lane_rel("IMPL"), tmp_path / WM.lane_rel("impl"))
    assert any(WM.lane_rel("IMPL") in x for x in _stale(tmp_path, "impl=all"))


def test_a_lane_file_nobody_names_is_reported_even_as_a_hard_link_to_a_current_one(tmp_path):
    WM.write_lanes(tmp_path, {"command": "c"}, "impl=all")
    os.link(tmp_path / WM.lane_rel("impl"), tmp_path / WM.lane_rel("alias"))
    assert any("alias" in x for x in _stale(tmp_path, "impl=all"))


def test_a_dangling_lane_symlink_is_reported_not_a_crash(tmp_path):
    entry = {"command": "c"}
    WM.write_lanes(tmp_path, entry, "foo=core")
    (tmp_path / WM.lane_rel("legacy")).symlink_to(tmp_path / "missing.json")
    assert any(
        "legacy" in x and "no longer" in x for x in WM.write_lanes(tmp_path, entry, "foo=core")
    )


def test_the_lane_parser_lives_in_the_config_layer_not_above_it():
    """The knob's `check` runs in `config_sections`, which may not import `services`."""
    from ddflow.config_sections import mcp as section

    assert WM.parse_lane_tiers is section.parse_lane_tiers


def _cfg(worker="core", lanes=""):
    return SimpleNamespace(mcp=SimpleNamespace(worker_tools=worker, lane_tools=lanes))


def test_the_doctor_counts_a_lane_on_the_whole_tool_list():
    env = {"ENABLE_TOOL_SEARCH": "false"}
    assert WM.doctor_notes(_cfg(), env) == []
    assert WM.doctor_notes(_cfg(lanes="impl=standard"), env) == []
    (note,) = WM.doctor_notes(_cfg(lanes="impl=standard,rev=all"), env)
    cause = WM.deferral_note(env, tier="all").partition(": ")[0]
    assert note == (
        f"{cause}: lower these lanes in `[mcp].lane_tools` and run `ddflow adopt` "
        f"({WM.lane_rel('rev')})"
    )


def test_the_doctor_says_each_place_that_runs_the_whole_tool_list():
    env = {"ENABLE_TOOL_SEARCH": "false"}
    worker, lane = WM.doctor_notes(_cfg(worker="all", lanes="rev=all"), env)
    assert worker == WM.deferral_note(env, tier="all")
    assert lane == WM.doctor_notes(_cfg(lanes="rev=all"), env)[0]
    assert WM.doctor_notes(_cfg(worker="all"), env) == [worker]


def test_the_doctor_survives_a_hand_edited_bad_lane_list():
    assert WM.doctor_notes(_cfg(lanes="impl=bogus"), {"ENABLE_TOOL_SEARCH": "false"}) == []


def test_a_hand_edited_bad_lane_list_stops_adopt_with_the_reason_not_a_traceback(repo):
    run_cli(repo, "init")
    with (repo / ".ddflow/config.toml").open("a") as f:
        f.write('\n[mcp]\nlane_tools = "impl=bogus"\n')
    code, out, err = run_cli(repo, "adopt", "--agents", "claude")
    assert code != 0 and "impl=bogus" in out + err and "Traceback" not in out + err
