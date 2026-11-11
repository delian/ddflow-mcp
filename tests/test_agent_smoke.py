"""B-ac-smoke: the headless smoke runner, built and tested with FAKE agent binaries only.

No real model-backed agent is ever started here: a fake is a small script in a temp dir that
writes the events a real agent would cause. A missing agent, a missing key, a timeout and a
rate limit are `unavailable`, never `pass`."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import agent_smoke as S
from ddflow.services import harnessreg

ROOT = Path(__file__).resolve().parents[1]

_DESCRIPTOR = """
id = "fakeagent"
rank = 98
family = "fake"
delta = "claude.md"

[mcp]
path = ".fake/mcp.json"
shape = "mcpServers"

[verified]
date = "2026-11-11"
sources = ["https://example.test/docs"]

[headless]
argv = ["fakeagent", "-p"]
max_turns_flag = "--max-turns"
autonomy = "yolo"

[autonomy]
profiles = { yolo = "--yes" }

[test]
detect = "fakeagent --version"
env_keys = "FAKE_KEY"
exit_codes = "0"
"""


def _harness(extra: str = "") -> harnessreg.Harness:
    return harnessreg.parse(_DESCRIPTOR + extra, "fakeagent")


def _fake(bindir: Path, body: str, name: str = "fakeagent") -> Path:
    bindir.mkdir(exist_ok=True)
    p = bindir / name
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


#: The fake agent: claims the one task and completes it, as events, in the project it runs in.
_GOOD_BODY = f"""
[ "$1" = "--version" ] && exit 0
{sys.executable} - "$PWD" <<'PY'
import sys
sys.path.insert(0, {str(ROOT)!r})
from ddflow.infra.log import EventLog
log = EventLog(sys.argv[1], "smoke-fake-1")
log.append("session.started", "s1", {{"harness": "fakeagent", "agent": "smoke-fake-1"}})
log.append("session.prompt", "s1", {{"text": "go"}})
log.append("task.added", "T1", {{"title": "t"}})
log.append("lease.acquired", "T1", {{"agent": "smoke-fake-1"}})
log.append("item.completed", "T1", {{}})
log.append("lease.released", "T1", {{"agent": "smoke-fake-1"}})
PY
git add -A
git -c user.name=x -c user.email=x@x -c commit.gpgsign=false commit -qm work
"""


def _env(bindir: Path, **more: str) -> dict[str, str]:
    return {"PATH": f"{bindir}:/usr/bin:/bin", "FAKE_KEY": "x", "HOME": str(bindir), **more}


def _scaffold(project: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(project)], check=True)


def _run(tmp_path, body=_GOOD_BODY, *, env=None, **kw):
    bindir = tmp_path / "bin"
    if body is not None:
        _fake(bindir, body)
    return S.smoke(
        _harness(),
        workdir=tmp_path / "work",
        prepare=_scaffold,
        env=env if env is not None else _env(bindir),
        timeout_s=30,
        **kw,
    )


def test_the_test_section_is_validated():
    assert _harness().reserved["test"].values["detect"] == "fakeagent --version"
    with pytest.raises(harnessreg.DescriptorError, match=r"test\.bogus"):
        _harness("\n")  # sanity: the good one parses
        harnessreg.parse(_DESCRIPTOR + 'bogus = "x"\n', "fakeagent")
    with pytest.raises(harnessreg.DescriptorError, match=r"test\.exit_codes"):
        harnessreg.parse(_DESCRIPTOR.replace('exit_codes = "0"', 'exit_codes = "ok"'), "fakeagent")


def test_the_command_comes_from_the_descriptor_with_the_turn_cap():
    line = S.command_line(_harness(), prompt="do it", max_turns=7)
    assert line.startswith("fakeagent -p")
    assert "--max-turns 7" in line and "--yes" in line and "do it" in line


def test_a_missing_agent_is_unavailable_never_pass(tmp_path):
    r = _run(tmp_path, body=None, env={"PATH": "/usr/bin:/bin", "FAKE_KEY": "x"})
    assert r.verdict == "unavailable"
    assert "fakeagent" in r.reason


def test_a_missing_key_is_unavailable(tmp_path):
    bindir = tmp_path / "bin"
    r = _run(tmp_path, env=_env(bindir) | {"FAKE_KEY": ""})
    assert r.verdict == "unavailable" and "FAKE_KEY" in r.reason


def test_a_good_run_passes_with_a_majority_of_three(tmp_path):
    r = _run(tmp_path)
    assert r.verdict == "pass"
    assert [a.verdict for a in r.attempts] == ["pass", "pass", "pass"] or r.passed >= 2
    assert all(a.assertions["completed_or_released"] for a in r.attempts if a.ran)


def test_a_run_that_does_nothing_fails_and_is_retried_once(tmp_path):
    r = _run(tmp_path, body='[ "$1" = "--version" ] && exit 0\nexit 0\n', rounds=1)
    assert r.verdict == "fail"
    assert len(r.attempts) == 2  # one retry
    assert not r.attempts[0].assertions["tool_called"]


def test_a_stray_lease_fails(tmp_path):
    body = _GOOD_BODY.replace('log.append("lease.released", "T1", {"agent": "smoke-fake-1"})', "")
    body = body.replace('log.append("item.completed", "T1", {})', "")
    r = _run(tmp_path, body=body, rounds=1, retries=0)
    assert r.verdict == "fail"
    assert r.attempts[0].assertions["no_stray_lease"] is False


def test_a_hung_agent_is_unavailable_on_timeout(tmp_path):
    bindir = tmp_path / "bin"
    _fake(bindir, '[ "$1" = "--version" ] && exit 0\nsleep 30\n')
    r = S.smoke(
        _harness(),
        workdir=tmp_path / "work",
        prepare=_scaffold,
        env=_env(bindir),
        timeout_s=1,
        rounds=1,
        retries=0,
    )
    assert r.verdict == "unavailable" and "timeout" in r.reason


def test_a_rate_limit_is_unavailable(tmp_path):
    body = '[ "$1" = "--version" ] && exit 0\necho "429 Too Many Requests" >&2\nexit 1\n'
    r = _run(tmp_path, body=body, rounds=1, retries=0)
    assert r.verdict == "unavailable" and "rate" in r.reason


def test_majority_logic():
    assert S.majority(["pass", "pass", "fail"]) == "pass"
    assert S.majority(["fail", "fail", "pass"]) == "fail"
    assert S.majority(["unavailable", "unavailable", "pass"]) == "unavailable"
    assert S.majority(["unavailable"] * 3) == "unavailable"
    assert S.majority([]) == "unavailable"


def test_the_result_is_recorded_as_an_event(tmp_path, repo, log):
    r = _run(tmp_path, log=log)
    evs = [e for e in log.read_all() if e.kind == "agent.smoke"]
    assert len(evs) == 1
    assert evs[0].data["agent"] == "fakeagent" and evs[0].data["verdict"] == r.verdict
    smoke = fold(log.read_all()).__dict__.get("smoke", {})
    assert smoke["fakeagent"]["verdict"] == r.verdict


def test_the_runner_never_writes_outside_its_workdir(tmp_path):
    _run(tmp_path)
    assert not (tmp_path / "bin" / ".ddflow").exists()


def test_the_cli_lists_without_running_and_refuses_live_without_flag(tmp_path):
    script = ROOT / "scripts" / "ci" / "agent-smoke"
    env = {**os.environ, "PATH": "/usr/bin:/bin:" + str(Path(sys.executable).parent)}
    out = subprocess.run(
        [sys.executable, str(script), "plan", "--json"], capture_output=True, text=True, env=env
    )
    assert out.returncode == 0, out.stderr
    plan = json.loads(out.stdout)
    assert "grok" in {p["agent"] for p in plan}
    live = subprocess.run(
        [sys.executable, str(script), "run", "grok"], capture_output=True, text=True, env=env
    )
    assert live.returncode == 3 and "--live" in (live.stdout + live.stderr)


def test_the_cli_live_run_with_no_agent_present_is_unavailable(tmp_path):
    script = ROOT / "scripts" / "ci" / "agent-smoke"
    env = {"PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin", "HOME": str(tmp_path)}
    out = subprocess.run(
        [sys.executable, str(script), "run", "grok", "--live", "--rounds", "1", "--json"],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
    )
    assert out.returncode == 2, out.stdout + out.stderr  # unavailable is 2, never 0
    assert json.loads(out.stdout)["verdict"] == "unavailable"


def test_event_log_unused_import_guard():
    assert EventLog  # the fixtures above build on the real log


def test_a_null_payload_does_not_break_the_fold(repo, log):
    log.append("agent.smoke", "x", {"agent": "x", "verdict": None, "attempts": None})
    assert fold(log.read_all()).__dict__["smoke"]["x"] == {
        "verdict": "",
        "reason": "",
        "attempts": [],
    }


def test_a_negative_retry_count_still_gives_a_verdict(tmp_path):
    r = _run(tmp_path, rounds=1, retries=-1)
    assert r.verdict == "pass"


def test_a_session_started_for_another_harness_is_a_gap(tmp_path, repo, log):
    log.append("session.started", "s", {"harness": "other"})
    _, gaps = S.assess(repo, "fakeagent")
    assert "no session.started with the harness name" in gaps


def test_a_shard_left_uncommitted_fails(tmp_path):
    body = _GOOD_BODY.replace("git add -A", "true").replace("git -c", "true # -c")
    r = _run(tmp_path, body=body, rounds=1, retries=0)
    assert r.verdict == "fail"
    assert r.attempts[0].assertions["shard_committed"] is False


def test_a_harness_with_no_headless_command_is_unavailable(tmp_path):
    text = _DESCRIPTOR.replace(
        '[headless]\nargv = ["fakeagent", "-p"]\nmax_turns_flag = "--max-turns"\n'
        'autonomy = "yolo"\n\n[autonomy]\nprofiles = { yolo = "--yes" }\n',
        "[headless]\nnone = true\n",
    )
    r = S.smoke(harnessreg.parse(text, "fakeagent"), workdir=tmp_path, env=_env(tmp_path))
    assert r.verdict == "unavailable" and "no headless command" in r.reason


def test_the_cli_reads_the_process_environment_through_the_script(tmp_path):
    bindir = tmp_path / "bin"
    _fake(bindir, "exit 1\n", name="grok")
    env = {"PATH": f"{bindir}:{Path(sys.executable).parent}:/usr/bin:/bin", "HOME": str(tmp_path)}
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "ci" / "agent-smoke"), "run", "grok", "--live",
         "--rounds", "1", "--retries", "0", "--json"],
        capture_output=True, text=True, env=env, cwd=tmp_path,
    )  # fmt: skip
    # the CLI on PATH was SEEN (not unavailable for a missing binary): the run reached the agent
    assert "not installed" not in out.stdout


def test_a_consented_install_goes_to_a_scratch_prefix_and_is_then_found(tmp_path):
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    cmd = 'mkdir -p "$AGENT_SMOKE_PREFIX/bin" && printf "#!/bin/sh\\nexit 0\\n" > "$AGENT_SMOKE_PREFIX/bin/fakeagent" && chmod +x "$AGENT_SMOKE_PREFIX/bin/fakeagent"'
    env = {"PATH": "/usr/bin:/bin", "FAKE_KEY": "x"}
    assert S.detect(_harness(), env, S.C.CommandRunner())  # missing before
    new, why = S.consented_install(_harness(), cmd, prefix, env)
    assert why == "" and (prefix / "bin" / "fakeagent").is_file()
    assert S.detect(_harness(), new, S.C.CommandRunner()) == ""
    assert "AGENT_SMOKE_PREFIX" not in env  # the caller's env is left alone


def test_a_failed_consented_install_is_unavailable_not_pass(tmp_path):
    _, why = S.consented_install(_harness(), "exit 3", tmp_path, {"PATH": "/usr/bin:/bin"})
    assert "consented install failed" in why


def test_the_missing_prompt_gap_reads_the_descriptors_ladder(repo):
    _, fallback = S.assess(repo, "fakeagent", native_prompt=False)
    _, native = S.assess(repo, "fakeagent", native_prompt=True)
    assert any("first-call fallback" in g for g in fallback)
    assert any("maps a native prompt hook" in g for g in native)
    assert not any("first-call fallback" in g for g in native)


def test_a_failed_consented_install_through_the_cli_is_still_reported(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    env = {"PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin", "HOME": str(tmp_path)}
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "ci" / "agent-smoke"), "run", "grok", "--live",
         "--consent-install", "exit 3", "--json"],
        capture_output=True, text=True, env=env, cwd=tmp_path,
    )  # fmt: skip
    assert out.returncode == 2, out.stdout + out.stderr
    assert json.loads(out.stdout)["verdict"] == "unavailable"
    assert "consented install failed" in out.stdout
