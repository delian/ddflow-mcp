"""Pins today's agent-identity answers (B-uni-identity.1-pin), before the resolvers merge.

Identity drives provenance fencing and reviewer independence, so every layer that names
an agent is pinned as a table: ``--agent``, ``DDFLOW_AGENT``, ``[agent].id``, a harness's
``ddflow_identify`` declaration, MCP ``_meta['ddflow/agent']`` / ``as_agent``
(D-mcp-identity-per-call), and the worktree-derived default. The later slices move the
resolvers into ``services.identity.resolve`` and must leave every row here unchanged; a
row that is wrong today is filed as a bug, never edited quietly.
"""

from __future__ import annotations

import argparse
import os
import socket
from pathlib import Path

import pytest

from ddflow.config import Config
from ddflow.core import agentname
from ddflow.infra import harness_identity
from ddflow.infra import log as L
from ddflow.infra.log import EventLog
from ddflow.services import approval
from ddflow.services import identity as ID
from ddflow.services.export import select as export_select
from ddflow.surfaces import mcp
from ddflow.surfaces.context import Ctx

HOST = "box"


@pytest.fixture(autouse=True)
def _clean_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DDFLOW_AGENT", raising=False)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setattr(socket, "gethostname", lambda: f"{HOST}.example.com")
    L._AGENT_ID_CACHE.clear()


@pytest.fixture
def adopted(repo: Path) -> Path:
    (repo / ".ddflow").mkdir()
    return repo


def _suffix(root: Path) -> str:
    return (root / L.CLONE_ID_FILE).read_text("utf-8").strip()


# -- the derived default ------------------------------------------------------------


def test_derived_id_before_adoption_is_host_and_tree_without_a_suffix(repo, monkeypatch):
    monkeypatch.chdir(repo)
    assert L.bare_agent_id(repo) == f"{HOST}-proj"
    assert L.default_agent_id(repo) == f"{HOST}-proj"
    assert not (repo / ".ddflow").exists(), "deriving an id must not adopt the repository"


def test_derived_id_after_adoption_carries_the_clone_suffix(adopted, monkeypatch):
    monkeypatch.chdir(adopted)
    got = L.default_agent_id(adopted)
    suffix = _suffix(adopted)
    assert len(suffix) == 6
    assert got == f"{HOST}-proj-{suffix}"
    assert L.bare_agent_id(adopted) == f"{HOST}-proj"
    assert L.tree_agent_ids(adopted, adopted) == {f"{HOST}-proj", got}


def test_a_linked_worktree_derives_its_own_name_and_the_clones_suffix(
    adopted, tmp_path, monkeypatch
):
    import subprocess

    tree = tmp_path / "wt-a"
    subprocess.run(
        ["git", "-C", str(adopted), "worktree", "add", "-q", "-b", "b", str(tree)], check=True
    )
    monkeypatch.chdir(tree)
    suffix = L._clone_suffix(adopted)
    assert L.default_agent_id(adopted) == f"{HOST}-wt-a-{suffix}"
    assert L.bare_agent_id(adopted) == f"{HOST}-wt-a"
    assert L.tree_agent_ids(tree, adopted) == {f"{HOST}-wt-a", f"{HOST}-wt-a-{suffix}"}


def test_the_cwd_of_another_repository_does_not_lend_its_name(adopted, tmp_path, monkeypatch):
    import subprocess

    other = tmp_path / "elsewhere"
    other.mkdir()
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    monkeypatch.chdir(other)
    assert L.bare_agent_id(adopted) == f"{HOST}-proj"


def test_the_hostname_is_the_short_form_in_the_derived_id(repo, monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: "plain")
    monkeypatch.chdir(repo)
    assert L.bare_agent_id(repo) == "plain-proj"


# -- ID.resolve: the layers, and WHICH layer won ----------------------------------

CFG_ID = "cfg-agent"


def _cfg(repo: Path, *, config_id: str = "", env_sourced: bool = False) -> Config:
    cfg = Config.load(repo)
    if config_id:
        cfg.agent.id = config_id
        cfg.sources["agent.id"] = "default" if env_sourced else "project"
    return cfg


@pytest.mark.parametrize(
    ("declared", "env", "config_id", "want", "layer"),
    [
        ("exp", "", "", "exp", "explicit"),
        ("exp", "envy", CFG_ID, "exp", "explicit"),
        ("", "envy", "", "envy", "env"),
        ("", "envy", CFG_ID, CFG_ID, "config"),
        ("", "", CFG_ID, CFG_ID, "config"),
        ("", "", "", None, "derived"),
    ],
)
def test_resolve_precedence_and_layer(adopted, monkeypatch, declared, env, config_id, want, layer):
    monkeypatch.chdir(adopted)
    if env:
        monkeypatch.setenv("DDFLOW_AGENT", env)
    cfg = _cfg(adopted, config_id=config_id)
    who, got_layer = ID.resolve(adopted, cfg, declared)
    assert got_layer == layer
    assert who == (want if want is not None else L.default_agent_id(adopted))


def test_without_a_config_the_env_wins_over_derived_and_nothing_is_validated(adopted, monkeypatch):
    monkeypatch.chdir(adopted)
    monkeypatch.setenv("DDFLOW_AGENT", "has space/and slash")
    assert tuple(ID.resolve(adopted, None)) == ("has space/and slash", "env")
    assert tuple(ID.resolve(adopted, None, "not valid either!")) == (
        "not valid either!",
        "explicit",
    )


def test_an_empty_event_log_agent_falls_past_the_env_var_to_the_derived_id(adopted, monkeypatch):
    # EventLog(repo, "") reads neither DDFLOW_AGENT nor the declaration (known gap).
    monkeypatch.chdir(adopted)
    monkeypatch.setenv("DDFLOW_AGENT", "envy")
    assert EventLog(adopted, "").agent_id == L.default_agent_id(adopted)
    assert EventLog(adopted, "named").agent_id == "named"


def test_the_shard_name_sanitises_by_isalnum_which_accepts_unicode(adopted):
    assert EventLog(adopted, "a b/c").shard.name == "a_b_c.jsonl"
    assert EventLog(adopted, "é-1").shard.name == "é-1.jsonl"
    assert EventLog(adopted, "x:y").shard.name == "x_y.jsonl"


# -- the CLI Ctx: --agent, DDFLOW_AGENT, the harness declaration ------------------------


def _ctx(repo: Path, agent: str = "") -> Ctx:
    return Ctx(argparse.Namespace(repo=str(repo), agent=agent, json=False))


def test_cli_agent_flag_wins_and_is_reported_explicit(adopted, monkeypatch):
    monkeypatch.chdir(adopted)
    monkeypatch.setenv("DDFLOW_AGENT", "envy")
    c = _ctx(adopted, "flagged")
    assert (c.log.agent_id, c.cfg.sources["agent.id"], c.requested_agent) == (
        "flagged",
        "explicit",
        "flagged",
    )


def test_cli_env_var_is_reported_env(adopted, monkeypatch):
    monkeypatch.chdir(adopted)
    monkeypatch.setenv("DDFLOW_AGENT", "envy")
    c = _ctx(adopted)
    assert (c.log.agent_id, c.cfg.sources["agent.id"], c.requested_agent) == ("envy", "env", "")


def test_cli_derived_when_nothing_is_set(adopted, monkeypatch):
    monkeypatch.chdir(adopted)
    c = _ctx(adopted)
    assert c.log.agent_id == L.default_agent_id(adopted)
    assert c.cfg.sources["agent.id"] == "derived"
    assert c.requested_agent == ""


def test_cli_does_not_validate_the_agent_name(adopted, monkeypatch):
    monkeypatch.chdir(adopted)
    assert _ctx(adopted, "weird name!").log.agent_id == "weird name!"


needs_proc = pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="reads /proc")


@needs_proc
def test_a_harness_declaration_names_the_cli_agent_unless_flag_or_env_is_given(
    adopted, monkeypatch
):
    monkeypatch.chdir(adopted)
    d = harness_identity._dir(adopted)
    assert d is not None
    d.mkdir(parents=True)
    pid = os.getppid()
    st = harness_identity._stat(pid)
    assert st is not None
    (d / f"{pid}-{st[2]}").write_text("declared-agent\n")
    assert harness_identity.declared(adopted) == "declared-agent"
    c = _ctx(adopted)
    assert (c.log.agent_id, c.cfg.sources["agent.id"], c.requested_agent) == (
        "declared-agent",
        "ddflow_identify",
        "declared-agent",
    )
    assert _ctx(adopted, "flagged").log.agent_id == "flagged"
    monkeypatch.setenv("DDFLOW_AGENT", "envy")
    assert _ctx(adopted).log.agent_id == "envy"
    (d / f"{pid}-{st[2]}").write_text("bad name!\n")
    monkeypatch.delenv("DDFLOW_AGENT")
    assert harness_identity.declared(adopted) == "", "a declared name that is not a name is ignored"


# -- MCP: the connection default, _meta and as_agent --------------------------------------


@pytest.mark.parametrize(
    "name",
    ["a", "alpha", "A.b_c-9", "x" * 64],
)
def test_mcp_accepts_these_agent_names(name):
    assert agentname.is_valid(name)


@pytest.mark.parametrize("name", ["", "x" * 65, "a b", "a/b", "a\n", "é", "a:b"])
def test_mcp_refuses_these_agent_names(name):
    assert not agentname.is_valid(name)


def test_mcp_default_agent_reports_the_layer(adopted, monkeypatch):
    monkeypatch.chdir(adopted)
    who, why = mcp._default_agent(adopted)
    assert (who, why) == (L.default_agent_id(adopted), "derived from the working tree")
    monkeypatch.setenv("DDFLOW_AGENT", "envy")
    assert mcp._default_agent(adopted) == ("envy", "from DDFLOW_AGENT")


@pytest.mark.parametrize(
    ("params", "want"),
    [
        ({}, ("", "")),
        ({"_meta": {}}, ("", "")),
        ({"_meta": "x"}, ("", "")),
        ({"_meta": {mcp.META_AGENT: "alpha"}}, ("alpha", "")),
        ({"_meta": {mcp.META_AGENT: "  alpha  "}}, ("alpha", "")),
        ({"_meta": {mcp.META_AGENT: "   "}}, ("", "")),
    ],
)
def test_meta_agent_accepted(params, want):
    assert mcp._meta_agent(params) == want


@pytest.mark.parametrize("value", ["bad name", "a/b", "x" * 65, 7])
def test_meta_agent_refused_with_a_reason(value):
    who, why = mcp._meta_agent({"_meta": {mcp.META_AGENT: value}})
    assert who == ""
    assert "is not a usable agent name" in why


def test_the_connection_caller_precedence(adopted, monkeypatch):
    monkeypatch.chdir(adopted)
    srv = mcp.Server(adopted, "conn")
    meta = {"_meta": {mcp.META_AGENT: "per-meta"}}
    # initialize-era: _meta is ignored, as_agent wins, the connection is the default
    assert srv._caller(meta, {}, False) == ("conn", "", {}, "")
    assert srv._caller({}, {mcp.AS_AGENT: "sub"}, False) == ("sub", "sub", {}, "")
    # stateless: _meta names the call; as_agent still wins when both are given
    assert srv._caller(meta, {}, True) == ("per-meta", "per-meta", {}, "")
    assert srv._caller(meta, {mcp.AS_AGENT: "sub"}, True) == ("sub", "sub", {}, "")
    assert srv._caller({}, {}, True) == ("conn", "", {}, "")
    # refusals keep the connection's identity and say why
    who, per_call, args, bad = srv._caller({}, {mcp.AS_AGENT: "bad name"}, False)
    assert (who, per_call, args) == ("conn", "", {}) and "not a usable agent name" in bad
    who, per_call, args, bad = srv._caller({}, {mcp.AS_AGENT: 5}, False)
    assert (who, per_call) == ("conn", "") and bad == f"{mcp.AS_AGENT} must be a string"
    who, per_call, _a, bad = srv._caller({"_meta": {mcp.META_AGENT: "no good"}}, {}, True)
    assert (who, per_call) == ("conn", "") and "not a usable agent name" in bad


def test_naming_the_connections_own_identity_per_call_is_not_someone_else(adopted, monkeypatch):
    monkeypatch.chdir(adopted)
    srv = mcp.Server(adopted, "conn")
    assert not srv._someone_else("")
    assert not srv._someone_else("conn")
    assert srv._someone_else("sub")
    # with no declared agent, the connection's own identity is the effective default
    anon = mcp.Server(adopted)
    assert not anon._someone_else(L.default_agent_id(adopted))
    assert anon._someone_else("sub")


# -- "is this an agent's invocation?" (approval, export) ----------------------------------


def test_agent_marker_table(monkeypatch):
    assert approval.agent_marker() == ""
    assert approval.agent_marker("kilo") == "--agent kilo"
    monkeypatch.setenv("DDFLOW_AGENT", "envy")
    assert approval.agent_marker() == "DDFLOW_AGENT=envy"
    assert approval.agent_marker("kilo") == "--agent kilo", "the flag is named before the env var"
    monkeypatch.delenv("DDFLOW_AGENT")
    monkeypatch.setenv("CLAUDECODE", "1")
    assert approval.agent_marker() == "CLAUDECODE is set (an agent harness's shell)"


def test_export_is_agent_follows_agent_marker_except_over_mcp(monkeypatch):
    assert export_select._is_agent("", True) == "the MCP surface"
    assert export_select._is_agent("", False) == ""
    assert export_select._is_agent("kilo", False) == "--agent kilo"
    monkeypatch.setenv("DDFLOW_AGENT", "envy")
    assert export_select._is_agent("", False) == "DDFLOW_AGENT=envy"


# -- one name rule, one common_dir (B-uni-identity.3-resolver.1-names) -------------------


def test_every_surface_shares_the_one_agent_name_pattern():
    assert not hasattr(mcp, "_VALID_AGENT"), "the surface asks core.agentname, not a copy"
    assert agentname.is_valid("A.b_c-9") and not agentname.is_valid("a\n")
    assert agentname.refusal("a b") == (
        "'a b' is not a usable agent name: use letters, digits, '.', '_' or '-', "
        "up to 64 characters."
    )


def test_common_dir_agrees_for_a_primary_checkout_and_a_linked_worktree(
    adopted, tmp_path, monkeypatch
):
    import subprocess

    from ddflow.infra import paths

    tree = tmp_path / "wt-c"
    subprocess.run(
        ["git", "-C", str(adopted), "worktree", "add", "-q", "-b", "c", str(tree)], check=True
    )
    want = (adopted / ".git").resolve()
    assert paths.common_dir(adopted) == paths.common_dir(tree) == want
    assert paths.common_dir(adopted, ask_git=False) == paths.common_dir(tree, ask_git=False) == want
    assert L._common_dir(str(tree)) == L._common_dir(str(adopted)) == str(want)
    assert harness_identity._dir(tree) == harness_identity._dir(adopted) == want / "ddflow-identity"


def test_common_dir_asks_git_only_when_the_files_say_nothing(adopted, tmp_path):
    from ddflow.infra import paths

    sub = adopted / "pkg"
    sub.mkdir()
    assert paths.common_dir(sub, ask_git=False) is None
    assert paths.common_dir(sub) == (adopted / ".git").resolve()
    assert paths.common_dir(tmp_path / "nowhere") is None
    assert paths.common_dir(tmp_path) is None


def test_primary_checkout_ignores_a_separate_git_dir_and_an_unreadable_pointer(tmp_path):
    import subprocess

    from ddflow.infra import paths

    sgd = tmp_path / "gitrepo" / ".git"
    (tmp_path / "gitrepo" / "ddflow").mkdir(parents=True)
    (tmp_path / "gitrepo" / "ddflow" / "__init__.py").write_text("")
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", f"--separate-git-dir={sgd}", str(work)], check=True)
    # a gitdir with no commondir is its own common dir, but it is no linked worktree
    assert paths.common_dir(work, ask_git=False) == sgd.resolve()
    assert paths.primary_checkout(work) is None
    for junk in (b"gitdir: foo\x00bar\n", b"gitdir: \xff\xfe\n", b"not a pointer\n"):
        (work / ".git").write_bytes(junk)
        assert paths.primary_checkout(work) is None  # and no exception
    (work / ".git").write_bytes(b"gitdir: foo\x00bar\n")
    assert paths.common_dir(work, ask_git=False) is None


def test_a_dangling_or_empty_git_pointer_is_no_repository(tmp_path):
    from ddflow.infra import paths

    work = tmp_path / "wt"
    work.mkdir()
    for line in (f"gitdir: {tmp_path / 'gone'}\n", "gitdir:\n", "gitdir:   \n"):
        (work / ".git").write_text(line)
        assert paths.common_dir(work, ask_git=False) is None, line
        assert paths.primary_checkout(work) is None


# -- services.identity.resolve / bind / open_log (B-uni-identity.3-resolver.2-resolve) -----


def test_bind_writes_the_identity_and_its_layer_back_only_when_it_differs(adopted, monkeypatch):
    from ddflow.services import identity as ID

    monkeypatch.chdir(adopted)
    cfg = _cfg(adopted)
    ID.bind(cfg, ID.resolve(adopted, cfg))
    assert (cfg.agent.id, cfg.sources["agent.id"]) == (L.default_agent_id(adopted), "derived")
    cfg2 = _cfg(adopted, config_id=CFG_ID)
    before = dict(cfg2.sources)
    ID.bind(cfg2, ID.resolve(adopted, cfg2))
    assert cfg2.sources == before, "an identity the config already carries is not re-sourced"


def test_open_log_opens_as_the_resolved_agent_and_can_bind_the_config(adopted, monkeypatch):
    from ddflow.services import identity as ID

    monkeypatch.chdir(adopted)
    monkeypatch.setenv("DDFLOW_AGENT", "envy")
    cfg = _cfg(adopted)
    log, who = ID.open_log(adopted, cfg, bind_cfg=True, lock_timeout_s=1.5)
    assert (log.agent_id, tuple(who)) == ("envy", ("envy", "env"))
    assert (cfg.agent.id, cfg.sources["agent.id"]) == ("envy", "env")
    log2, who2 = ID.open_log(adopted, _cfg(adopted), "flagged")
    assert (log2.agent_id, who2.source) == ("flagged", "explicit")


@pytest.mark.parametrize(
    ("requested", "env", "want"),
    [
        ("alpha", {"DDFLOW_AGENT": "beta", "CLAUDECODE": "1"}, "--agent alpha"),
        ("", {"DDFLOW_AGENT": "beta", "CLAUDECODE": "1"}, "DDFLOW_AGENT=beta"),
        ("", {"CLAUDECODE": "1"}, "CLAUDECODE is set (an agent harness's shell)"),
        ("", {}, ""),
    ],
)
def test_agent_marker_answers_the_same_for_every_caller(monkeypatch, requested, env, want):
    """One marker rule behind approval, reviewer_trust and the export enable."""
    from ddflow.services import approval as AP
    from ddflow.services import identity as ID
    from ddflow.services import reviewer_trust as RT

    for var in ("DDFLOW_AGENT", *ID.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)
    for var, value in env.items():
        monkeypatch.setenv(var, value)
    assert ID.agent_marker(requested) == want
    assert AP.agent_marker(requested) == RT.agent_marker(requested) == want
    assert export_select._is_agent(requested, False) == want
    assert ID.is_agent(requested, False) == want
    assert ID.is_agent(requested, True) == "the MCP surface"


def test_only_services_identity_defines_or_reads_the_agent_marker():
    """A private copy of the rule drifts: the harness variable, the function name and the
    marker table each appear only in services/identity (and its two re-exports)."""
    root = Path(__file__).resolve().parents[1] / "ddflow"
    reexports = {"services/approval.py", "services/reviewer_trust.py"}
    for token, allowed in (
        ("CLAUDECODE", {"services/identity.py"}),
        ("def agent_marker", {"services/identity.py"}),
        ("HARNESS_MARKERS", {"services/identity.py"} | reexports),
    ):
        homes = {
            p.relative_to(root).as_posix() for p in root.rglob("*.py") if token in p.read_text()
        }
        assert homes <= allowed, (token, homes - allowed)
