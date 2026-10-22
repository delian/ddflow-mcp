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
import subprocess
import sys
import time
from pathlib import Path

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
        while True:
            m = json.loads(self.p.stdout.readline())
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
    assert {"codex", "gemini", "qwen", "kimi", "opencode", "cursor", "copilot"} <= set(rows)
    assert all(v["list_changed"] == "unavailable" for v in rows.values())


def test_kilo_recipe_warns_about_session_share():
    mod = _script()
    assert any("share" in w.lower() for w in mod.RECIPES["kilo"]["warn"])


def test_recipes_use_dummy_prompts_only():
    mod = _script()
    for name, rec in mod.RECIPES.items():
        assert "bump" in rec["prompt"], name
