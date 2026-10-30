"""Lane-private hook config, identity, consent and the hook-fires probe (SW-lane-hooks)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from ddflow.services.swarm import conformance as C
from ddflow.services.swarm import lanehooks as LH
from ddflow.services.swarm import lanehooks_check as CK
from ddflow.services.swarm import lanehooks_plugin as PL


@pytest.fixture(autouse=True)
def _isolated_git(monkeypatch, tmp_path):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    monkeypatch.setenv("HOME", str(tmp_path))
    for v in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        monkeypatch.delenv(v, raising=False)


def sh(cwd, *a):
    return subprocess.run(a, cwd=cwd, capture_output=True, text=True, check=True)


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
    home = tmp_path / "lane-home"
    home.mkdir()
    return repo, wt, home


def approved(harness):
    row = {"human": True, "digest": PL.digest(), "token_hash": "", "used_at": "", "user": "op"}
    return SimpleNamespace(approvals={LH.consent_subject(harness): [row]})


# --- strength from descriptors -------------------------------------------------------------


def test_strength_table_from_descriptors():
    assert LH.strength("claude").level == LH.PREVENT
    assert LH.strength("claude").door == "settings-arg"
    k = LH.strength("kilo")
    assert (k.level, k.door, k.consent) == (LH.PREVENT, "plugin-home", True)
    for h in ("grok", "gemini", "codex", "no-such-agent"):  # no descriptor or no verified door
        s = LH.strength(h)
        assert (s.level, s.door) == (LH.DETECT, "git-pre-commit") and s.reason


def test_unverified_door_is_detect_unless_allowed():
    s = LH.strength("opencode")
    assert s.level == LH.DETECT and "unverified" in s.reason
    assert LH.strength("opencode", allow_unverified=True).level == LH.PREVENT


# --- claude: settings file in the lane home, lane identity ---------------------------------


def test_claude_hook_is_private_and_carries_the_lane_identity(tree):
    repo, wt, home = tree
    p = LH.prepare(repo, wt, home, "claude", "lane-7")
    assert isinstance(p, LH.Prepared) and p.level == LH.PREVENT
    assert p.env == {"DDFLOW_AGENT": "lane-7"}
    assert p.args == ("--settings", str(home / LH.DIR_NAME / "claude-settings.json"))
    assert all(home in f.parents for f in p.files)
    cmd = json.loads(p.files[0].read_text())["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert "--agent lane-7" in cmd and str(repo) in cmd
    assert not (wt / ".claude").exists() and not (repo / ".claude").exists()
    assert sh(wt, "git", "status", "--porcelain").stdout == ""
    assert (p.files[0].stat().st_mode & 0o777) == 0o600


def test_claude_probe_fires_the_written_hook(tree):
    repo, wt, home = tree
    LH.prepare(repo, wt, home, "claude", "lane-7")
    assert LH.hook_probe(home)() is True


def test_probe_fails_when_the_written_config_is_gone_or_wrong(tree):
    repo, wt, home = tree
    p = LH.prepare(repo, wt, home, "claude", "lane-7")
    p.files[0].write_text(json.dumps({"hooks": {"PreToolUse": [{"hooks": [{"command": "true"}]}]}}))
    assert LH.hook_probe(home)() is False
    p.files[0].unlink()
    assert LH.hook_probe(home)() is False


def test_probe_is_unavailable_with_no_manifest(tmp_path):
    assert LH.hook_probe(tmp_path)() is None


# --- plugin harnesses: consent first, then lane-home plugin --------------------------------


def test_plugin_needs_consent_and_writes_nothing(tree):
    repo, wt, home = tree
    c = LH.prepare(repo, wt, home, "kilo", "lane-7", state=None)
    assert isinstance(c, LH.Consent)
    assert c.subject == "lane-plugin:kilo" and c.digest == PL.digest()
    assert c.diff.startswith("--- /dev/null") and "tool.execute.before" in c.diff
    assert list(home.iterdir()) == []
    stale = approved("kilo")
    stale.approvals["lane-plugin:kilo"][0]["digest"] = "old"  # an earlier template
    assert isinstance(LH.prepare(repo, wt, home, "kilo", "lane-7", state=stale), LH.Consent)
    agent_row = approved("kilo")
    agent_row.approvals["lane-plugin:kilo"][0]["human"] = False  # an agent never approves
    assert isinstance(LH.prepare(repo, wt, home, "kilo", "lane-7", state=agent_row), LH.Consent)


def test_kilo_plugin_lands_in_the_lane_config_dir(tree):
    repo, wt, home = tree
    p = LH.prepare(repo, wt, home, "kilo", "lane-7", state=approved("kilo"))
    assert isinstance(p, LH.Prepared)
    assert p.files == (home / ".config/kilo/plugin" / PL.FILE_NAME,)
    text = p.files[0].read_text()
    assert '"lane-7"' in text and "__DDFLOW" not in text
    assert str(repo) in text
    assert sh(wt, "git", "status", "--porcelain").stdout == ""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run the plugin")
def test_kilo_plugin_probe_runs_the_plugin_and_it_throws(tree):
    repo, wt, home = tree
    LH.prepare(repo, wt, home, "kilo", "lane-7", state=approved("kilo"))
    assert LH.hook_probe(home)() is True


def test_plugin_values_are_data_not_code():
    out = PL.render(["py", "-m", 'x"; throw 1; "'], 'a"b')
    assert '["py", "-m", "x\\"; throw 1; \\""]' in out and '"a\\"b"' in out
    with pytest.raises(ValueError):
        PL.render([], "a")


# --- detect fallback and removal -----------------------------------------------------------


def test_harness_without_a_door_falls_back_to_the_git_hook_and_is_reported(tree):
    repo, wt, home = tree
    p = LH.prepare(repo, wt, home, "grok", "lane-9")
    assert isinstance(p, LH.Prepared) and p.level == LH.DETECT and p.files == ()
    assert "detection at commit only" in p.note
    assert githook_installed(wt)
    assert LH.hook_probe(home)() is None  # never the 'hook' level
    LH.remove(home)
    assert not githook_installed(wt)


def githook_installed(wt):
    from ddflow.services.swarm import githook

    return githook.installed(wt)


def test_remove_deletes_exactly_what_was_written(tree):
    repo, wt, home = tree
    p = LH.prepare(repo, wt, home, "claude", "lane-7")
    (home / "other.txt").write_text("keep")
    assert LH.listed(home)[0] == p.files[0]
    LH.remove(home)
    assert not p.files[0].exists() and not (home / LH.DIR_NAME).exists()
    assert (home / "other.txt").exists()
    assert LH.remove(home) == "no lane hook config here"


def test_unavailable_not_guessed(tree):
    repo, wt, home = tree
    for bad in ("", "a b", "a/b"):
        assert LH.prepare(repo, wt, home, "claude", bad).status == "unavailable"
    inside = wt / "home"
    r = LH.prepare(repo, wt, inside, "claude", "lane-7")
    assert r.status == "unavailable" and "inside" in r.detail and not inside.exists()
    r = LH.prepare(repo, wt, repo / ".ddflow" / "x", "claude", "lane-7")
    assert isinstance(r, LH.Prepared)  # the project's git-ignored state dir is a private place
    assert sh(repo, "git", "status", "--porcelain").stdout == ""


# --- the check the hook runs ---------------------------------------------------------------


def test_paths_and_probe_refusal(tmp_path):
    assert CK.paths_of({"tool_input": {"file_path": "a", "x": 1}}) == ["a"]
    assert CK.paths_of({"args": {"filePath": "b"}}) == ["b"]
    assert CK.paths_of({"tool_input": {"command": "ls"}}) == [] and CK.paths_of([]) == []
    code, msg = CK.verdict(tmp_path, "l", [f"{CK.PROBE_DIR}/x"], tmp_path)
    assert code == CK.REFUSE and "hook fired" in msg


def test_verdict_uses_the_lane_lease(tmp_path, monkeypatch):
    monkeypatch.setattr(CK, "_mine", lambda repo, agent: (["src/a/**", "x.py"], ["CHANGELOG.md"]))
    ok = ["src/a/b.py", "x.py", "CHANGELOG.md", ".ddflow/events/e.json"]
    assert CK.verdict(tmp_path, "l", [str(tmp_path / p) for p in ok], tmp_path) == (0, "")
    code, msg = CK.verdict(tmp_path, "l", ["src/b.py", "/etc/passwd"], tmp_path)
    assert code == CK.REFUSE and "src/b.py" in msg and "outside the lane worktree" in msg
    monkeypatch.setattr(CK, "_mine", lambda repo, agent: ([], []))  # lease gone: stop
    code, msg = CK.verdict(tmp_path, "l", ["src/a/b.py"], tmp_path)
    assert code == CK.REFUSE and "no live lease" in msg
    assert CK.verdict(tmp_path, "l", [], tmp_path) == (0, "")  # a shell call carries no path


def test_check_fails_open(tmp_path, monkeypatch, capsys):
    def boom(*a):
        raise RuntimeError("secret /path")

    monkeypatch.setattr(CK, "verdict", boom)
    monkeypatch.setattr("sys.stdin", SimpleNamespace(read=lambda: "{}"))
    assert CK.main(["--repo", str(tmp_path), "--agent", "l"]) == 0
    err = capsys.readouterr().err
    assert "not checked" in err and "secret" not in err


def test_probe_reaches_the_hook_level_in_conformance(tree):
    repo, wt, home = tree
    LH.prepare(repo, wt, home, "claude", "lane-7")
    o = C.Observed(started=True, stopped_clean=True, tool_events=True)
    o.hook = LH.hook_probe(home)()
    rep = C.grade("lane-7", o, C.Params())
    hook = next(x for x in rep.outcomes if x.check == "hook")
    assert hook.status == C.PASSED


def test_review_findings_hold(tree, tmp_path):
    repo, wt, home = tree
    # an outside path is outside even when the lease is broad
    assert CK._relative("/etc/passwd", Path("/lane/repo")) is None
    assert CK._relative("../x", Path("/lane/repo")) is None
    assert CK._relative("src/a.py", Path("/lane/repo")) == "src/a.py"
    # a value holding a marker is never substituted twice
    out = PL.render(["/bin/c", "__DDFLOW_AGENT__"], "lane-1")
    assert '["/bin/c", "__DDFLOW_AGENT__"]' in out and '""lane-1""' not in out
    # a manifest cannot make remove delete outside the lane home
    victim = tmp_path / "important.txt"
    victim.write_text("keep")
    LH.prepare(repo, wt, home, "claude", "lane-7")
    m = home / LH.DIR_NAME / LH.MANIFEST
    d = json.loads(m.read_text())
    d["files"].insert(0, str(victim))  # first: a partial cleanup would delete the rest
    m.write_text(json.dumps(d))
    assert "refused" in LH.remove(home) and victim.exists()
    assert not (home / LH.DIR_NAME).exists()  # the legitimate files went, the victim stayed
    # a tracked hook path is refused (relative pathspec, absolute home under the repo)
    h2 = repo / ".ddflow" / "h2"
    (h2 / LH.DIR_NAME).mkdir(parents=True)
    f = h2 / LH.DIR_NAME / "claude-settings.json"
    f.write_text("{}")
    sh(repo, "git", "add", "-f", str(f))
    r = LH.prepare(repo, wt, h2, "claude", "lane-7")
    assert getattr(r, "status", "") == "unavailable" and "tracked" in r.detail


def test_second_round_findings(tree, tmp_path):
    repo, wt, home = tree
    assert CK._self_managed(".ddflow/x") and CK._self_managed("AGENTS.md")
    assert not CK._self_managed(".ddflow-evil/README.md") and not CK._self_managed("AGENTS.md.x")
    with pytest.raises(ValueError):
        PL.render("/bin/check", "lane-1")
    # a symlink at a hook path is refused, its target untouched
    target = tmp_path / "bashrc"
    target.write_text("keep")
    (home / LH.DIR_NAME).mkdir()
    (home / LH.DIR_NAME / "claude-settings.json").symlink_to(target)
    with pytest.raises(OSError):
        LH.prepare(repo, wt, home, "claude", "lane-7")
    assert target.read_text() == "keep"
    # a malformed manifest makes the probe False, never a crash
    (home / LH.DIR_NAME / "claude-settings.json").unlink()
    (home / LH.DIR_NAME / LH.MANIFEST).write_text(json.dumps({"level": "prevent", "files": []}))
    assert LH.hook_probe(home)() is False
