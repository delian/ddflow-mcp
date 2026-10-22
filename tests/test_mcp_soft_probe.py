"""SU-client-probe-ci: the list_changed probe server, its log classifier and the CI script.

The probe is the appendix-A server of docs/ddflow/research/mcp-client-matrix.md. A client
honours `notifications/tools/list_changed` iff it sends `tools/list` AFTER the notification
(R-su-probe-logrule). The script runs it under an installed agent CLI; a missing CLI is
`unavailable` (exit 2), never a pass.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ci" / "mcp-client-probe"

GOVERNS = ("ddflow/mcpshim/**", "scripts/ci/mcp-client-probe")


class Client:
    """A scripted MCP client driving the probe server over its stdio."""

    def __init__(self, tmp_path: Path, *extra: str):
        self.log = tmp_path / "probe.log"
        self.p = subprocess.Popen(
            [sys.executable, "-m", "ddflow.mcpshim.probe_server", str(self.log), *extra],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            cwd=ROOT,
        )
        self.lines: queue.Queue = queue.Queue()
        threading.Thread(
            target=lambda: [self.lines.put(x) for x in self.p.stdout], daemon=True
        ).start()
        self.notes: list[dict] = []
        self._id = 0

    def call(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        self.p.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}})
            + "\n"
        )
        self.p.stdin.flush()
        return self.read_until(self._id)

    def read_until(self, want_id: int | None) -> dict:
        if want_id is None and self.notes:
            return self.notes.pop(0)
        while True:
            try:
                m = json.loads(self.lines.get(timeout=8))
            except queue.Empty:
                self.p.kill()
                raise AssertionError("probe server stopped answering") from None
            if m.get("id") == want_id and want_id is not None:
                return m
            if "id" not in m:
                self.notes.append(m)
                if want_id is None:
                    return m

    def close(self) -> str:
        self.p.stdin.close()
        self.p.wait(timeout=10)
        return self.log.read_text()


def _analyze():
    from ddflow.mcpshim import probe_server

    return probe_server.analyze_log


def _tool_names(resp: dict) -> list[str]:
    return [t["name"] for t in resp["result"]["tools"]]


def test_bump_adds_a_tool_and_notifies_at_once(tmp_path):
    c = Client(tmp_path)
    c.call("initialize", {"protocolVersion": "2025-06-18"})
    assert "secret_oracle" not in _tool_names(c.call("tools/list"))
    r = c.call("tools/call", {"name": "bump"})
    assert "secret_oracle" not in json.dumps(r), "the new tool's name must not be disclosed"
    note = c.read_until(None)
    assert note["method"] == "notifications/tools/list_changed"
    assert "secret_oracle" not in json.dumps(note)
    assert "secret_oracle" in _tool_names(c.call("tools/list"))
    c.close()


def test_delaybump_notifies_later_while_idle(tmp_path):
    c = Client(tmp_path, "--delay", "0.3")
    c.call("initialize", {"protocolVersion": "2025-06-18"})
    c.call("tools/call", {"name": "delaybump"})
    t0 = time.time()
    note = c.read_until(None)
    assert note["method"] == "notifications/tools/list_changed"
    assert time.time() - t0 >= 0.2
    c.close()


def test_the_new_tool_answers_with_its_word(tmp_path):
    c = Client(tmp_path)
    c.call("initialize", {"protocolVersion": "2025-06-18"})
    c.call("tools/call", {"name": "bump"})
    r = c.call("tools/call", {"name": "secret_oracle"})
    assert r["result"]["content"][0]["text"] == "MAGENTA"
    c.close()


def test_every_message_is_logged_with_direction(tmp_path):
    c = Client(tmp_path)
    c.call("initialize", {"protocolVersion": "2025-06-18"})
    log = c.close()
    dirs = [line.split()[1] for line in log.splitlines()]
    assert dirs == ["IN", "OUT"]


def _honouring(tmp_path):
    c = Client(tmp_path)
    c.call(
        "initialize",
        {"protocolVersion": "2025-06-18", "clientInfo": {"name": "fake", "version": "9.1"}},
    )
    c.call("tools/list")
    c.call("tools/call", {"name": "bump"})
    c.read_until(None)
    c.call("tools/list")
    return c.close()


def _ignoring(tmp_path):
    c = Client(tmp_path)
    c.call("initialize", {"protocolVersion": "2025-06-18"})
    c.call("tools/list")
    c.call("tools/call", {"name": "bump"})
    c.read_until(None)
    return c.close()


def test_a_refetch_after_the_notification_means_honours(tmp_path):
    row = _analyze()(_honouring(tmp_path))
    assert row["list_changed"] == "honours"
    assert row["client"] == {"name": "fake", "version": "9.1"}
    assert row["revision"] == "2025-06-18"


def test_no_refetch_means_ignores(tmp_path):
    assert _analyze()(_ignoring(tmp_path))["list_changed"] == "ignores"


def test_no_notification_sent_is_unknown_not_ignores(tmp_path):
    c = Client(tmp_path)
    c.call("initialize", {"protocolVersion": "2025-06-18"})
    c.call("tools/list")
    assert _analyze()(c.close())["list_changed"] == "unknown"


def test_an_empty_log_is_unknown():
    row = _analyze()("")
    assert row["list_changed"] == "unknown" and row["client"] is None


def test_listen_and_discover_are_reported():
    log = "\n".join(
        [
            '1.00 IN  {"jsonrpc":"2.0","id":1,"method":"server/discover","params":{}}',
            '1.01 IN  {"jsonrpc":"2.0","id":2,"method":"subscriptions/listen","params":{}}',
        ]
    )
    row = _analyze()(log)
    assert row["probed_discover"] is True and row["listen"] is True


def test_a_refetch_before_the_notification_does_not_count():
    log = "\n".join(
        [
            '1.0 IN  {"jsonrpc":"2.0","id":1,"method":"tools/list"}',
            '2.0 OUT {"jsonrpc":"2.0","method":"notifications/tools/list_changed"}',
        ]
    )
    assert _analyze()(log)["list_changed"] == "ignores"


def test_a_truncated_or_junk_line_is_skipped():
    log = 'garbage\n1.0 OUT {"jsonrpc": "2.0", "meth\n'
    assert _analyze()(log)["list_changed"] == "unknown"


# ---- the script ----------------------------------------------------------------------


def _script():
    loader = importlib.machinery.SourceFileLoader("mcp_client_probe", str(SCRIPT))
    spec = importlib.util.spec_from_loader("mcp_client_probe", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _run(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    import os

    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**os.environ, **(env or {})},
    )


def test_analyze_subcommand_prints_the_row(tmp_path):
    log = tmp_path / "x.log"
    log.write_text(_honouring(tmp_path))
    r = _run("analyze", str(log))
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["list_changed"] == "honours"


def test_missing_cli_is_unavailable_exit_2(tmp_path):
    r = _run("row", "claude", env={"PATH": str(tmp_path)})
    assert r.returncode == 2
    assert "unavailable" in r.stdout
    assert json.loads(r.stdout)["list_changed"] == "unavailable"


def test_harness_without_a_recipe_is_unavailable_exit_2():
    r = _run("row", "no-such-harness")
    assert r.returncode == 2
    assert json.loads(r.stdout)["reason"].startswith("no recipe")


def test_matrix_lists_every_unknown_cell_and_never_passes_a_missing_one(tmp_path):
    r = _run("matrix", env={"PATH": str(tmp_path)})
    assert r.returncode == 2
    rows = json.loads(r.stdout)
    unknown = {
        "codex",
        "gemini",
        "qwen",
        "kimi",
        "opencode",
        "cursor",
        "copilot",
        "antigravity",
        "grok-build",
        "windsurf",
        "amp",
        "droid",
    }
    assert unknown <= set(rows)
    assert all(v["list_changed"] == "unavailable" for v in rows.values())


def test_kilo_recipe_warns_about_session_share():
    mod = _script()
    assert any("share" in w.lower() for w in mod.RECIPES["kilo"]["warn"])


def test_recipes_use_dummy_prompts_only():
    mod = _script()
    for name, rec in mod.RECIPES.items():
        assert "bump" in rec["prompt"], name


def test_a_notification_before_the_response_is_not_lost_by_the_test_client(tmp_path):
    c = Client(tmp_path)
    c.notes.append({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
    assert c.read_until(None)["method"] == "notifications/tools/list_changed"
    c.close()


def test_a_pipelined_tools_list_is_logged_before_the_notification(tmp_path):
    """R: a client that sent tools/list BEFORE it could have seen the notification must not
    classify as honouring (rubber_duck #1): IN is logged on arrival, not when handled."""
    c = Client(tmp_path)
    c.call("initialize", {"protocolVersion": "2025-06-18"})
    c.p.stdin.write(
        json.dumps({"jsonrpc": "2.0", "id": 90, "method": "tools/call", "params": {"name": "bump"}})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 91, "method": "tools/list"})
        + "\n"
    )
    c.p.stdin.flush()
    c.read_until(90)
    c.read_until(91)
    assert _analyze()(c.close())["list_changed"] == "ignores"


def test_delay_without_a_value_prints_usage_and_exits_2(tmp_path):
    r = subprocess.run(
        [sys.executable, "-m", "ddflow.mcpshim.probe_server", str(tmp_path / "l"), "--delay"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert r.returncode == 2 and "usage" in r.stderr and "Traceback" not in r.stderr


def test_a_timeout_still_classifies_what_the_log_recorded(tmp_path):
    mod = _script()
    fake = tmp_path / "bin"
    fake.mkdir()
    # a "claude" that drives the probe like an honouring client, then hangs
    (fake / "claude").write_text(
        f"#!{sys.executable}\n"
        "import json, subprocess, sys, time\n"
        "cfg = json.load(open(sys.argv[sys.argv.index('--mcp-config') + 1]))['mcpServers']['lc']\n"
        "p = subprocess.Popen([cfg['command'], *cfg['args']], stdin=subprocess.PIPE,\n"
        "                     stdout=subprocess.PIPE, text=True)\n"
        "def req(i, m, params=None):\n"
        "    p.stdin.write(json.dumps({'jsonrpc':'2.0','id':i,'method':m,'params':params or {}})+'\\n')\n"
        "    p.stdin.flush()\n"
        "    while json.loads(p.stdout.readline()).get('id') != i: pass\n"
        "req(1, 'initialize', {'protocolVersion': 'x'}); req(2, 'tools/call', {'name': 'bump'})\n"
        "time.sleep(0.2); req(3, 'tools/list'); time.sleep(60)\n"
    )
    (fake / "claude").chmod(0o755)
    import os

    old = os.environ["PATH"]
    os.environ["PATH"] = str(fake) + os.pathsep + old
    try:
        row, code = mod.probe_row("claude", timeout=3)
    finally:
        os.environ["PATH"] = old
    assert row["list_changed"] == "honours" and "timed out" in row["reason"]
    assert code == 1


def test_delaybump_does_not_add_the_tool_before_it_notifies(tmp_path):
    c = Client(tmp_path, "--delay", "60")  # never fires inside the test: no timing involved
    c.call("initialize", {"protocolVersion": "2025-06-18"})
    r = c.call("tools/call", {"name": "delaybump"})
    assert "list your tools" not in json.dumps(r), "the result must not prompt a refetch"
    assert "secret_oracle" not in json.dumps(r)
    assert "secret_oracle" not in _tool_names(c.call("tools/list"))
    c.p.kill()


def test_delaybump_adds_the_tool_with_its_notification(tmp_path):
    c = Client(tmp_path, "--delay", "0.2")
    c.call("initialize", {"protocolVersion": "2025-06-18"})
    c.call("tools/call", {"name": "delaybump"})
    note = c.read_until(None)
    assert "secret_oracle" not in json.dumps(note)
    assert "secret_oracle" in _tool_names(c.call("tools/list"))
    c.close()


def test_add_and_notify_are_one_step_under_the_lock():
    """critic: a tools/list handled between the add and the notification would show the tool
    before the notification. A snapshot requested the moment the tool is appended must not
    complete before the notification has been sent."""
    import io
    import threading

    from ddflow.mcpshim import probe_server as ps

    probe = ps._Probe(io.StringIO(), io.StringIO(), 0)
    events, threads = [], []

    class Tools(list):
        def append(self, item):
            super().append(item)
            t = threading.Thread(target=lambda: (probe._snapshot(), events.append("snapshot")))
            threads.append(t)
            t.start()

    class YieldingLock:
        """Gives a waiting thread a window every time the lock is released."""

        def __init__(self):
            self._l = threading.RLock()

        def __enter__(self):
            self._l.acquire()

        def __exit__(self, *exc):
            self._l.release()
            time.sleep(0.1)

    probe.lock = YieldingLock()
    probe.tools = Tools(probe.tools)
    real_send = probe.send
    probe.send = lambda m: (events.append("notify"), real_send(m))
    probe._add_and_notify()
    threads[0].join(timeout=5)
    assert events == ["notify", "snapshot"], events


def test_every_unavailable_row_has_the_documented_keys():
    mod = _script()
    row = mod._unavailable("x", "why")
    assert {
        "harness",
        "list_changed",
        "reason",
        "client",
        "revision",
        "probed_discover",
        "listen",
    } <= set(row)


def test_logging_is_not_truncated(tmp_path):
    big = {
        "protocolVersion": "x",
        "clientInfo": {"name": "huge", "version": "1"},
        "capabilities": {"experimental": {"x": "y" * 200_000}},
    }
    c = Client(tmp_path)
    c.call("initialize", big)
    c.call("tools/list")
    log = c.close()
    assert _analyze()(log)["client"] == {"name": "huge", "version": "1"}
    out_tools = [ln for ln in log.splitlines() if " OUT " in ln and '"result": {"tools"' in ln]
    assert out_tools and json.loads(out_tools[0].split(None, 2)[2])["result"]["tools"]


def test_an_unavailable_row_keeps_what_the_log_already_showed(tmp_path):
    mod = _script()
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "claude").write_text(
        f"#!{sys.executable}\n"
        "import json, subprocess, sys\n"
        "cfg = json.load(open(sys.argv[sys.argv.index('--mcp-config') + 1]))['mcpServers']['lc']\n"
        "p = subprocess.Popen([cfg['command'], *cfg['args']], stdin=subprocess.PIPE,\n"
        "                     stdout=subprocess.PIPE, text=True)\n"
        "p.stdin.write(json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize','params':\n"
        "    {'protocolVersion':'r9','clientInfo':{'name':'c','version':'2'}}})+'\\n')\n"
        "p.stdin.flush(); p.stdout.readline(); p.stdin.close(); p.wait(); sys.exit(1)\n"
    )
    (fake / "claude").chmod(0o755)
    import os

    old = os.environ["PATH"]
    os.environ["PATH"] = str(fake) + os.pathsep + old
    try:
        row, code = mod.probe_row("claude", timeout=20)
    finally:
        os.environ["PATH"] = old
    assert row["list_changed"] == "unavailable" and code == 2
    assert row["revision"] == "r9" and row["client"] == {"name": "c", "version": "2"}


@pytest.mark.parametrize(
    "junk", ["null", "7", '"x"', "[1]", '{"id": 5, "method": "tools/call", "params": "x"}']
)
def test_valid_json_that_is_not_a_request_does_not_kill_the_responder(tmp_path, junk):
    c = Client(tmp_path)
    c.p.stdin.write(junk + "\n")
    c.p.stdin.flush()
    assert _tool_names(c.call("tools/list")) == ["bump", "delaybump"]
    c.close()


def test_analyze_skips_valid_json_that_is_not_an_object():
    assert _analyze()("1.0 IN  null\n2.0 OUT [1]\n")["list_changed"] == "unknown"


def test_a_cli_that_failed_before_the_probe_ran_is_unavailable_not_unknown(tmp_path):
    mod = _script()
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "claude").write_text("#!/bin/sh\necho 'no credentials' >&2\nexit 1\n")
    (fake / "claude").chmod(0o755)
    import os

    old = os.environ["PATH"]
    os.environ["PATH"] = str(fake) + os.pathsep + old
    try:
        row, code = mod.probe_row("claude", timeout=10)
    finally:
        os.environ["PATH"] = old
    assert row["list_changed"] == "unavailable" and code == 2
    assert "no credentials" in row["reason"]


def test_bump_result_does_not_prompt_a_refetch(tmp_path):
    """A result that tells the model to list its tools makes a client that ignores
    list_changed look like one that honours it (critic #1)."""
    c = Client(tmp_path)
    c.call("initialize", {"protocolVersion": "2025-06-18"})
    r = c.call("tools/call", {"name": "bump"})
    assert "list your tools" not in json.dumps(r)
    c.close()


def test_the_oracle_does_not_answer_before_it_exists(tmp_path):
    c = Client(tmp_path)
    c.call("initialize", {"protocolVersion": "2025-06-18"})
    r = c.call("tools/call", {"name": "secret_oracle"})
    assert "MAGENTA" not in json.dumps(r)
    c.close()


def test_a_second_bump_sends_no_second_notification(tmp_path):
    c = Client(tmp_path)
    c.call("initialize", {"protocolVersion": "2025-06-18"})
    c.call("tools/call", {"name": "bump"})
    c.call("tools/call", {"name": "bump"})
    log = c.close()
    assert log.count("list_changed") == 1


def test_a_large_initialize_is_still_classified(tmp_path):
    big = {
        "protocolVersion": "2025-06-18",
        "clientInfo": {"name": "big", "version": "1"},
        "capabilities": {"experimental": {"x": "y" * 5000}},
    }
    c = Client(tmp_path)
    c.call("initialize", big)
    row = _analyze()(c.close())
    assert row["client"] == {"name": "big", "version": "1"}


def test_analyze_of_an_inconclusive_log_exits_2(tmp_path):
    log = tmp_path / "empty.log"
    log.write_text("")
    assert _run("analyze", str(log)).returncode == 2


def test_a_relative_path_entry_does_not_crash_the_row(tmp_path):
    (tmp_path / "claude").write_text("#!/bin/sh\nexit 0\n")
    (tmp_path / "claude").chmod(0o755)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "row", "claude"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={"PATH": "."},
    )
    assert "Traceback" not in r.stderr
    assert json.loads(r.stdout)["list_changed"] == "unavailable"


def test_a_cli_that_exits_0_without_calling_the_probe_is_unavailable(tmp_path):
    (tmp_path / "claude").write_text("#!/bin/sh\nexit 0\n")
    (tmp_path / "claude").chmod(0o755)
    r = _run("row", "claude", env={"PATH": str(tmp_path)})
    assert r.returncode == 2 and json.loads(r.stdout)["list_changed"] == "unavailable"


def test_the_oracle_check_reads_the_tool_list_under_the_lock():
    """standards: the membership check must not race the delaybump thread's append."""
    import io

    from ddflow.mcpshim import probe_server as ps

    probe = ps._Probe(io.StringIO(), io.StringIO(), 0)
    held = []

    class Tools(list):
        def __iter__(self):
            held.append(probe.lock._is_owned())
            return super().__iter__()

    probe.tools = Tools(probe.tools)
    probe._call(1, "secret_oracle")
    assert held and all(held), held
