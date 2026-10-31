"""SW-lane-opencode: the opencode/kilo lane behind the Lane contract, on stub binary and endpoint."""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ddflow.infra import paths
from ddflow.services import lane_driver as ld
from ddflow.services import modelreg
from ddflow.services.lanes import opencode as OC
from ddflow.services.swarm import conformance as cf
from ddflow.services.swarm import lanehooks as LH
from ddflow.services.swarm import lanehooks_plugin as PL

EXAMPLE = paths.package_dir() / "models" / "routes" / "local.example.toml"
SECRET = "sk-test-0123456789abcdef"
EVENTS = [
    {"type": "step_start", "sessionID": "ses_1"},
    {"type": "tool_use", "part": {"tool": "ddflow_claim", "state": {"input": {}}}},
    {"type": "tool_use", "part": {"tool": "ddflow_heartbeat", "state": {"input": {}}}},
    {"type": "tool_use", "part": {"tool": "ddflow_gate", "state": {"input": {"action": "record"}}}},
]
FAKE = """#!{py}
import json, os, sys
if "--version" in sys.argv:
    print("{ver}"); sys.exit(0)
json.dump({{"argv": sys.argv[1:], "home": os.environ.get("HOME"), "agent": os.environ.get("DDFLOW_AGENT"),
           "key": os.environ.get("LAN_ENDPOINT_KEY"), "cwd": os.getcwd()}}, open({rec!r}, "w"))
for e in {events!r}:
    print(json.dumps(e), flush=True)
"""


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    for v in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("LAN_ENDPOINT_KEY", SECRET)


def sh(cwd, *a):
    return subprocess.run(a, cwd=cwd, capture_output=True, text=True, check=True)


def fake_bin(tmp_path, name="kilo", ver="7.8.18", events=EVENTS):
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    rec = tmp_path / "record.json"
    p = d / name
    p.write_text(FAKE.format(py=sys.executable, ver=ver, rec=str(rec), events=events))
    p.chmod(0o755)
    return d, rec


@pytest.fixture
def tree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    sh(repo, "git", "init", "-q")
    sh(repo, "git", "config", "user.email", "a@b.c")
    sh(repo, "git", "config", "user.name", "a")
    (repo / ".git" / "info" / "exclude").write_text(".ddflow/\n")
    (repo / "a.txt").write_text("a")
    sh(repo, "git", "add", ".")
    sh(repo, "git", "commit", "-qm", "init")
    wt = tmp_path / "lane-wt"
    sh(repo, "git", "worktree", "add", "-q", "-b", "lane", str(wt))
    return repo, wt, tmp_path / "lane-home"


def cat(tmp_path):
    t = EXAMPLE.read_text().replace('s = "unverified"', 's = "verified"')
    (tmp_path / "models.toml").write_text(t)
    return modelreg.load(local_dir=tmp_path)


def approved(harness):
    row = {"human": True, "digest": PL.digest(), "token_hash": "", "used_at": "", "user": "op"}
    return SimpleNamespace(approvals={LH.consent_subject(harness): [row]})


def prep(tmp_path, tree, **kw):
    repo, wt, home = tree
    d, _ = fake_bin(tmp_path)
    b = OC.select(which=lambda n, path=None: str(d / n) if (d / n).exists() else None)
    args = {"repo": repo, "worktree": wt, "home": home, "agent": "lane-7", "prompt": "do the item",
                "item": "X-1", "state": approved("kilo"), "binary": b}  # fmt: skip
    args.update(kw)
    return OC.prepare(cat(tmp_path), "lan-vllm", **args)


# --- binary selection: opencode first, kilo when it is not installed -----------------------


def test_select_prefers_opencode_then_kilo_and_names_version(tmp_path):
    d, _ = fake_bin(tmp_path, "kilo", ver="7.8.18")
    only_kilo = lambda n, path=None: str(d / n) if (d / n).exists() else None  # noqa: E731
    b = OC.select(which=only_kilo)
    assert (b.name, b.version, b.read) == ("kilo", "7.8.18", True)
    assert "kilo 7.8.18" in b.describe()
    fake_bin(tmp_path, "opencode", ver="1.2.3")
    assert OC.select(which=only_kilo).name == "opencode"


def test_select_unread_version_is_said_and_none_installed_is_unavailable(tmp_path):
    d, _ = fake_bin(tmp_path, "kilo", ver="")
    b = OC.select(which=lambda n, path=None: str(d / n) if n == "kilo" else None)
    assert b.read is False and "unread" in b.describe()
    r = OC.select(which=lambda n, path=None: None)
    assert isinstance(r, ld.Result) and r.status == "unavailable"


# --- the private config --------------------------------------------------------------------


def test_prepare_writes_only_lane_private_files(tmp_path, tree):
    _repo, wt, home = tree
    lp = prep(tmp_path, tree)
    assert isinstance(lp, OC.LanePrep) and lp.level == LH.PREVENT
    cfgp = lp.endpoint.config
    assert cfgp == home / ".config/kilo/kilo.json" and stat.S_IMODE(cfgp.stat().st_mode) == 0o600
    doc = json.loads(cfgp.read_text())
    m = doc["mcp"]["ddflow"]
    assert (
        m["type"] == "local"
        and m["enabled"] is True
        and m["command"][-3:] == ["--agent", "lane-7", "mcp"]
    )
    assert m["environment"] == {"DDFLOW_AGENT": "lane-7"}
    instr = Path(doc["instructions"][0])
    assert home in instr.parents and "lane-7" in instr.read_text() and "X-1" in instr.read_text()
    assert (home / ".config/kilo/plugin" / PL.FILE_NAME).exists()
    assert sh(wt, "git", "status", "--porcelain").stdout == ""
    assert not (wt / "kilo.json").exists() and not (wt / ".kilo").exists()
    assert SECRET not in cfgp.read_text() and "{env:LAN_ENDPOINT_KEY}" in cfgp.read_text()


def test_spec_identity_key_by_name_and_no_shell(tmp_path, tree):
    _repo, wt, home = tree
    lp = prep(tmp_path, tree)
    s = lp.spec
    assert s.name == "lane-7" and s.cwd == str(wt) and s.home == home
    assert s.fixed_env == {"DDFLOW_AGENT": "lane-7"}
    assert s.env == {"LAN_ENDPOINT_KEY": "LAN_ENDPOINT_KEY"} and SECRET not in repr(s)
    assert "PATH" in s.pass_env
    assert (
        s.argv[1] == "run"
        and "--format" in s.argv
        and "--auto" in s.argv
        and s.argv[-2:] == ("--file", "{prompt_file}")
        and s.prompt == "do the item"
    )
    assert s.values["model"] == "ddflow-lan-vllm/SERVED-MODEL-ID"


def test_missing_consent_unproven_tools_and_no_binary_start_nothing(tmp_path, tree):
    repo, wt, home = tree
    c = prep(tmp_path, tree, state=None)
    assert isinstance(c, LH.Consent) and not (home / ".config/kilo/kilo.json").exists()
    (tmp_path / "models.toml").write_text(EXAMPLE.read_text())  # tool_call unverified again
    u = OC.prepare(modelreg.load(local_dir=tmp_path), "lan-vllm", repo=repo, worktree=wt,
                   home=tmp_path / "h2", agent="lane-7", prompt="p", state=approved("kilo"),
                   binary=OC.Binary("kilo", "/x", "1", True))  # fmt: skip
    assert isinstance(u, ld.Result) and "tool calls are not verified" in u.detail


def test_opencode_binary_is_a_verified_route_and_takes_auto(tmp_path, tree):
    b = OC.Binary("opencode", "/x/opencode", "1.19.0", True)
    lp = prep(tmp_path, tree, binary=b, state=approved("opencode"))
    assert isinstance(lp, OC.LanePrep) and lp.level == LH.PREVENT
    assert lp.endpoint.config == tree[2] / ".config/opencode/opencode.json"
    assert (tree[2] / ".config/opencode/plugins" / PL.FILE_NAME).exists()
    assert "--auto" in lp.spec.argv and lp.spec.argv[0] == "/x/opencode"


def test_binary_override_by_env_var_beats_path_and_stays_absolute(tmp_path):
    d, _ = fake_bin(tmp_path, "opencode", ver="1.19.0")
    off = lambda n, path=None: None  # noqa: E731
    env = {"DDFLOW_OPENCODE_BIN": str(d / "opencode")}
    seen = lambda n, path=None: n if n == str(d / "opencode") else None  # noqa: E731
    b = OC.select(which=seen, environ=env)
    assert (b.name, b.path, b.version) == ("opencode", str(d / "opencode"), "1.19.0")
    assert isinstance(OC.select(which=off, environ=env), ld.Result)
    rel = OC.select(
        which=lambda n, path=None: f"bin/{n}" if n == "kilo" else None,
        version=lambda p: "1",
        environ={},
    )
    assert rel.name == "kilo" and rel.path.startswith("/")


def test_agent_identity_is_required(tmp_path, tree):
    r = prep(tmp_path, tree, agent="bad name")
    assert isinstance(r, ld.Result) and "identity" in r.detail


# --- running it: stub binary, stub endpoint -------------------------------------------------


def test_lane_runs_headless_in_the_worktree_with_private_home(tmp_path, tree):
    _repo, wt, home = tree
    lp = prep(tmp_path, tree)
    rec = tmp_path / "record.json"
    drv = OC.OpenCodeLane()
    h = drv.start(lp.spec)
    assert not isinstance(h, ld.Result)
    evs = list(drv.events(h, idle_s=3))
    assert [e.event for e in evs] == ["started", "tool", "tool", "tool"]
    assert evs[1].fields["name"] == "ddflow_claim"
    assert "steer" not in h.capabilities and "usage" not in h.capabilities
    assert drv.steer(h, "x").status == "unavailable"
    drv.stop(h)
    seen = json.loads(rec.read_text())
    assert seen["home"] == str(home) and seen["agent"] == "lane-7" and seen["key"] == SECRET
    assert (
        seen["cwd"] == str(wt.resolve())
        and seen["argv"][0] == "run"
        and "do the item" not in seen["argv"]
    )
    assert SECRET not in json.dumps([e.fields for e in evs])
    assert drv.status(h).session == "ses_1"


def test_missing_key_variable_refuses_the_launch(tmp_path, tree, monkeypatch):
    lp = prep(tmp_path, tree)
    monkeypatch.delenv("LAN_ENDPOINT_KEY")
    r = OC.OpenCodeLane().start(lp.spec)
    assert isinstance(r, ld.Result) and r.status == "failed" and SECRET not in r.detail


def test_transport_is_registered():
    assert isinstance(ld.driver_for(OC.TRANSPORT), OC.OpenCodeLane)
    assert OC.LIVE_PROBE.status == "unavailable"


# --- graded by SW-conformance ---------------------------------------------------------------

P = cf.Params(min_accuracy=0.9, min_calls=3, idle_s=1.0, lease_wait_s=1.0,
              tools=frozenset({"claim", "heartbeat", "gate"}))  # fmt: skip


@pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run the plugin")
def test_conformance_grades_the_lane_through_its_own_hook(tmp_path, tree):
    lp = prep(tmp_path, tree)
    r = OC.conformance(lp, P)
    s = {o.check: o.status for o in r.outcomes}
    assert s["launch"] == s["claim"] == s["heartbeat"] == s["gate_record"] == cf.PASSED
    assert s["hook"] == cf.PASSED  # the written plugin refused the probe
    assert s["lease_stop"] == cf.UNAVAILABLE  # no revocation was given


def test_cleanup_removes_hook_files(tmp_path, tree):
    lp = prep(tmp_path, tree)
    assert (lp.home / ".config/kilo/plugin" / PL.FILE_NAME).exists()
    OC.cleanup(lp.home)
    assert not (lp.home / ".config/kilo/plugin" / PL.FILE_NAME).exists()


def test_select_with_no_extra_dirs_never_searches_cwd(tmp_path, monkeypatch):
    d, _ = fake_bin(tmp_path)
    monkeypatch.chdir(d)
    monkeypatch.setenv("PATH", "/nonexistent")
    assert isinstance(OC.select(extra_dirs=(), environ={}), ld.Result)


def test_instructions_without_an_item_do_not_cite_a_lease(tmp_path):
    text = OC.instructions("lane-7", "", tmp_path)
    assert "lease allows" not in text and "claim one" in text and "assess ddflow" in text


def test_override_expands_tilde(tmp_path, monkeypatch):
    d, _ = fake_bin(tmp_path, "opencode", ver="1.19.0")
    monkeypatch.setenv("HOME", str(tmp_path))
    seen = lambda n, path=None: n if n == str(d / "opencode") else None  # noqa: E731
    b = OC.select(which=seen, environ={"DDFLOW_OPENCODE_BIN": "~/bin/opencode"})
    assert b.path == str(d / "opencode")
