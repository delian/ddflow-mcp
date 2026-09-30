"""Bug B52751f12ed: two ways `ddflow companions` misreported a project that was fine.

1. **OptMem was still a default.** `ddflow memory` (35dd944) took over its job --
   operational facts about this machine, shown by `brief`, searched by `recall` -- yet
   the shipped registry kept `optmem` at `default = true`, so every project was told to
   install a second memory store beside the one ddflow now keeps. The `memory`
   companion's text still said ddflow's recall does not cover operational facts, and
   the `rules` gate was reported as having nothing behind it while ddflow itself serves
   it.

2. **A server was found by its NAME only.** run_nemo_run registers codeguide-mcp in
   `.mcp.json` as `coding-guides`, with exactly the companion's launch command. Looked
   up only under the id `codeguide`, it read "installed ... but no agent is configured
   to launch it", and `companions add` would have written a duplicate entry under the
   id beside the working one.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.services import companions as CO

CODEGUIDE = ["run", "--rm", "-i", "docker.io/delian/codeguide-mcp"]


def _reg(tmp_path) -> dict[str, CO.Companion]:
    return {c.id: c for c in CO.load(tmp_path)}


def _status(repo: Path, cid: str) -> CO.Status:
    return {st.companion.id: st for st in CO.scan(repo, probe=False)}[cid]


def _mcp_json(repo: Path, servers: dict) -> None:
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": servers}), "utf-8")


# -- 1. OptMem is not a default; ddflow memory serves `rules` --------------------------


def test_optmem_is_no_longer_a_default_recommendation(tmp_path):
    optmem = _reg(tmp_path)["optmem"]
    assert optmem.default is False, "optmem is still proposed although ddflow memory replaced it"
    assert "ddflow memory" in optmem.why, "optmem's why does not point at what replaced it"


def test_the_memory_companion_no_longer_claims_recall_misses_operational_facts(tmp_path):
    why = _reg(tmp_path)["memory"].why
    assert "ddflow memory" in why, why
    assert "does not belong in a committed log" not in why, why


def test_the_rules_gate_is_served_by_ddflow_memory_itself(tmp_path):
    """`rules` is `ddflow brief`, which shows the operational memory and ranked lessons.
    With no companion installed at all it still has ddflow behind it -- and only it:
    a gate ddflow does not serve must stay uncovered."""
    cover = CO.gate_coverage(tmp_path, [], ["rules", "implement"])
    assert cover["rules"] == [CO.BUILTIN_MEMORY], cover
    assert cover["implement"] == [], cover


def test_the_report_does_not_list_rules_as_uncovered(repo):
    run_cli(repo, "init")
    _, out, _ = run_cli(repo, "--json", "companions", "list", "--no-probe")
    data = json.loads(out)
    assert "rules" not in data["uncovered_gates"], data["uncovered_gates"]
    assert "optmem" not in [c["id"] for c in data["companions"] if c["default"]]


# -- 2. a server is found by its launch, not only its name ----------------------------


def test_a_server_under_another_name_with_the_same_launch_is_registered(tmp_path):
    """run_nemo_run's exact `.mcp.json` entry."""
    _mcp_json(tmp_path, {"coding-guides": {"command": "docker", "args": CODEGUIDE}})
    st = _status(tmp_path, "codeguide")
    assert st.state == "registered", (st.state, st.registered_in)
    assert st.registered_in == ["claude"]
    assert st.registered_as == {"claude": "coding-guides"}, "the report must name the key"


def test_the_report_names_the_key_it_is_registered_under(repo):
    run_cli(repo, "init")
    _mcp_json(repo, {"coding-guides": {"command": "docker", "args": CODEGUIDE}})
    _, out, _ = run_cli(repo, "companions", "list", "--no-probe")
    block = out[out.index("codeguide") :].split("\n\n", 1)[0]
    assert "registered for: claude" in block and "coding-guides" in block, block
    _, out, _ = run_cli(repo, "--json", "companions", "list", "--no-probe")
    cg = {c["id"]: c for c in json.loads(out)["companions"]}["codeguide"]
    assert cg["registered_as"] == {"claude": "coding-guides"}, cg


def test_a_name_match_wins_and_is_reported_under_the_id(tmp_path):
    _mcp_json(
        tmp_path,
        {
            "aaa": {"command": "docker", "args": CODEGUIDE},
            "codeguide": {"command": "docker", "args": CODEGUIDE},
        },
    )
    assert _status(tmp_path, "codeguide").registered_as == {"claude": "codeguide"}


def test_an_npx_version_tag_and_extra_flags_still_match(tmp_path):
    _mcp_json(
        tmp_path,
        {
            "ctx": {"command": "npx", "args": ["-y", "@upstash/context7-mcp@latest"]},
            "think": {
                "command": "/usr/local/bin/npx",
                "args": ["-y", "@modelcontextprotocol/server-sequential-thinking@2025.7.1"],
            },
            "guides": {
                "command": "docker",
                "args": ["run", "--rm", "-i", "-e", "TOKEN", "docker.io/delian/codeguide-mcp"],
            },
        },
    )
    assert _status(tmp_path, "context7").registered_as == {"claude": "ctx"}
    assert _status(tmp_path, "sequential").registered_as == {"claude": "think"}
    assert _status(tmp_path, "codeguide").registered_as == {"claude": "guides"}


def test_a_different_server_under_another_name_does_not_count(tmp_path):
    _mcp_json(
        tmp_path,
        {
            # Same command, a different image.
            "other": {"command": "docker", "args": ["run", "--rm", "-i", "docker.io/x/other-mcp"]},
            # Same args, a different command.
            "podman": {"command": "podman", "args": CODEGUIDE},
            # Arguments out of order.
            "shuffled": {
                "command": "docker",
                "args": ["run", "--rm", "docker.io/delian/codeguide-mcp", "-i"],
            },
            # A package whose name merely starts with the companion's.
            "fork": {"command": "npx", "args": ["-y", "@upstash/context7-mcp-fork"]},
            # A remote server has no launch at all.
            "remote": {"url": "https://mcp.example.invalid/mcp"},
        },
    )
    for cid in ("codeguide", "context7"):
        st = _status(tmp_path, cid)
        assert st.registered_in == [] and st.registered_as == {}, (cid, st.registered_as)
        assert st.state != "registered"


def test_a_command_array_config_matches_too(tmp_path):
    """opencode/Kilo store `command` as one array holding the arguments."""
    (tmp_path / ".kilo").mkdir()
    (tmp_path / ".kilo" / "kilo.json").write_text(
        json.dumps({"mcp": {"cg": {"type": "local", "command": ["docker", *CODEGUIDE]}}})
    )
    assert _status(tmp_path, "codeguide").registered_as == {"kilo": "cg"}


def test_a_toml_config_matches_by_launch_and_not_otherwise(tmp_path):
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text(
        '[mcp_servers.coding-guides]\ncommand = "docker"\n'
        'args = ["run", "--rm", "-i", "docker.io/delian/codeguide-mcp"]\n\n'
        '[mcp_servers.other]\ncommand = "npx"\nargs = ["-y", "some-other-mcp"]\n'
    )
    assert _status(tmp_path, "codeguide").registered_as == {"codex": "coding-guides"}
    assert _status(tmp_path, "context7").registered_as == {}


def test_register_does_not_duplicate_a_server_kept_under_another_name(tmp_path):
    _mcp_json(tmp_path, {"coding-guides": {"command": "docker", "args": CODEGUIDE}})
    c = _reg(tmp_path)["codeguide"]
    for dry in (True, False):
        status, msg = CO.register(tmp_path, c, "claude", dry_run=dry)
        assert status == "unchanged", (dry, status, msg)
        assert "coding-guides" in msg, msg
    servers = json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]
    assert set(servers) == {"coding-guides"}, servers


def test_register_does_not_duplicate_in_a_toml_config_either(tmp_path):
    (tmp_path / ".codex").mkdir()
    cfg = tmp_path / ".codex" / "config.toml"
    before = (
        '[mcp_servers.coding-guides]\ncommand = "docker"\n'
        'args = ["run", "--rm", "-i", "docker.io/delian/codeguide-mcp"]\n'
    )
    cfg.write_text(before)
    status, msg = CO.register(tmp_path, _reg(tmp_path)["codeguide"], "codex")
    assert status == "unchanged", (status, msg)
    assert cfg.read_text() == before


def test_register_still_writes_when_only_a_different_server_is_there(tmp_path):
    _mcp_json(tmp_path, {"other": {"command": "docker", "args": ["run", "-i", "x/other"]}})
    status, _ = CO.register(tmp_path, _reg(tmp_path)["codeguide"], "claude")
    assert status == "written"
    servers = json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]
    assert set(servers) == {"other", "codeguide"}


def test_a_server_key_with_braces_is_reported_not_crashed_on(tmp_path):
    """Found by roborev on 622a254: the message was built with the key interpolated and
    then `.format`-ed, so a key holding `{` or `}` raised instead of reporting."""
    _mcp_json(tmp_path, {"{guides}": {"command": "docker", "args": CODEGUIDE}})
    status, msg = CO.register(tmp_path, _reg(tmp_path)["codeguide"], "claude")
    assert status == "unchanged" and "`{guides}`" in msg, (status, msg)


def test_a_version_tag_is_only_dropped_from_an_npm_package_spec(tmp_path):
    """Found by the critic: the tag rule ran on EVERY argument, so two docker launches of
    one image against different hosts (`...@1db.example`, `...@2db.example`) collapsed to one
    server, and `companions add` would have refused to write the right entry."""
    db = CO.Companion(
        id="db",
        command="docker",
        args=["run", "-i", "mcp/postgres", "postgresql://u:p@1db.example"],
    )
    other = {
        "command": "docker",
        "args": ["run", "-i", "mcp/postgres", "postgresql://u:p@2db.example"],
    }
    assert not CO.launches_as(db, other)
    tagged = CO.Companion(id="t", command="docker", args=["run", "-i", "secret@2024"])
    assert not CO.launches_as(tagged, {"command": "docker", "args": ["run", "-i", "secret@2025"]})
    # ...while an npx package still matches across a version tag.
    ctx = CO.Companion(id="c", command="npx", args=["-y", "@upstash/context7-mcp"])
    assert CO.launches_as(ctx, {"command": "npx", "args": ["-y", "@upstash/context7-mcp@2.0.1"]})


def test_another_server_given_the_companion_as_an_argument_is_not_it():
    """Found by the critic: "the companion's args in order, anything between" accepted an
    entry that launches ANOTHER package or image and merely passes the companion's as an
    argument. Only flags (and a flag's value) may sit between the companion's arguments;
    anything after them is the server's own configuration."""
    fs = CO.Companion(
        id="fs", command="npx", args=["-y", "@modelcontextprotocol/server-filesystem"]
    )
    gh = ["-y", "@modelcontextprotocol/server-github", "@modelcontextprotocol/server-filesystem"]
    assert not CO.launches_as(fs, {"command": "npx", "args": gh})
    a = CO.Companion(id="a", command="docker", args=["run", "img-a"])
    assert not CO.launches_as(a, {"command": "docker", "args": ["run", "img-b", "img-a"]})
    # Found by roborev on dfc13e3: a BOOLEAN flag before the other image let it through,
    # because every flag was assumed to take the next token as its value.
    for flag in ("-i", "--rm", "-it"):
        entry = {"command": "docker", "args": ["run", flag, "img-b", "img-a"]}
        assert not CO.launches_as(a, entry), flag
    assert not CO.launches_as(fs, {"command": "npx", "args": ["-y", "--quiet", *gh[1:]]})
    # Found by the critic: a flag's VALUE equal to the companion's image is not the image.
    assert not CO.launches_as(a, {"command": "docker", "args": ["run", "-e", "img-a", "alpine"]})
    # Found by roborev on 5160e3e: common value-taking docker flags must be known ones.
    known = (("-h", "host"), ("--gpus", "all"), ("--dns", "dns.example"), ("--pids-limit", "128"))
    for flag, value in known:
        entry = {"command": "docker", "args": ["run", flag, value, "img-a"]}
        assert CO.launches_as(a, entry), flag
    # An UNLISTED flag is taken as boolean, so its value reads as another image: a missed
    # registration (a duplicate on `add`) is the chosen failure, never a false "covered".
    assert not CO.launches_as(a, {"command": "docker", "args": ["run", "--made-up", "v", "img-a"]})
    # A flag with its value between them, and the server's own arguments after, still match.
    assert CO.launches_as(a, {"command": "docker", "args": ["run", "--env=X", "-i", "img-a"]})
    assert CO.launches_as(a, {"command": "docker", "args": ["run", "-e", "TOKEN", "img-a"]})
    assert CO.launches_as(
        fs, {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/srv"]}
    )


def test_a_cli_companion_on_rules_still_counts_beside_ddflow_memory(tmp_path):
    """Raised by the rubber duck: the kinds tests moved to `dedupe`, so nothing checked a
    companion on a gate ddflow ALSO serves. Both are named; neither hides the other."""
    optmem = CO.Status(_reg(tmp_path)["optmem"], True, [], "")
    assert CO.gate_coverage(tmp_path, [optmem], ["rules"])["rules"] == [CO.BUILTIN_MEMORY, "optmem"]


def test_register_does_not_refresh_a_stale_id_into_a_duplicate(tmp_path):
    """Found by the critic: with a STALE entry under the id and the right launch under
    another name, `register` rewrote the id to that launch -- two names, one server."""
    stale = {"command": "docker", "args": ["run", "old/image"]}
    _mcp_json(
        tmp_path, {"codeguide": stale, "coding-guides": {"command": "docker", "args": CODEGUIDE}}
    )
    status, msg = CO.register(tmp_path, _reg(tmp_path)["codeguide"], "claude")
    assert status == "unchanged" and "coding-guides" in msg, (status, msg)
    assert json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]["codeguide"] == stale


def test_a_malformed_args_field_is_no_launch_not_a_crash(tmp_path):
    """Found by the rubber duck: an array `command` merged `args` before its type was
    checked, so `"args": 5` raised out of the scan and `"args": "run img"` split into
    characters."""
    (tmp_path / ".kilo").mkdir()
    for bad in (5, "run --rm -i docker.io/delian/codeguide-mcp"):
        (tmp_path / ".kilo" / "kilo.json").write_text(
            json.dumps({"mcp": {"cg": {"type": "local", "command": ["docker"], "args": bad}}})
        )
        assert _status(tmp_path, "codeguide").registered_as == {}, bad
