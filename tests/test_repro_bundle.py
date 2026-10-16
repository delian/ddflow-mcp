"""`bug found` stores a reproduction bundle with the bug (DX-bug-repro-bundle).

Operator 2026-10-16: why are so many bugs not reproducible? The writer's context was lost:
ddflow version and tree, environment (DDFLOW_RUNNING_GATE hid B29d63a65a2), config layer,
the item's state. These pin that the bundle exists, carries those facts, is redacted, is
capped, can be switched off, and is read back.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import state_as_reader as state

from ddflow import __version__
from ddflow.api import knowledge as K
from ddflow.api import show
from ddflow.core import blobref as B
from ddflow.infra.log import EventLog
from ddflow.services import logblobs
from ddflow.services import reprobundle as RB

SECRET = "AKIAIOSFODNN7EXAMPLE"


@pytest.fixture
def proj(repo: Path, monkeypatch) -> Path:
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "Phase one"})
    log.append("task.added", "T1", {"parent": "P1", "title": "parser", "globs": ["src/p.py"]})
    monkeypatch.chdir(repo)
    return repo


def _config(repo: Path, text: str) -> None:
    p = repo / ".ddflow" / "config.toml"
    p.write_text((p.read_text() if p.exists() else "") + text)


def _file(repo: Path, **kw):
    out = K.bug_found(repo, summary="it breaks", item=kw.pop("item", "T1"), agent="a1", **kw)
    assert out.ok, out.reason
    return out.data["id"], state(repo).bugs[out.data["id"]]


def _bundle(repo: Path, bug) -> dict:
    got = RB.read(repo, bug.repro)
    assert isinstance(got, dict), got
    return got


def test_bug_carries_a_bundle_with_the_facts(proj, monkeypatch):
    monkeypatch.setenv("DDFLOW_RUNNING_GATE", "ci")
    _config(proj, '[bugs]\nphase = "fixes"\n')
    bid, bug = _file(proj, repro_cmd="uv run pytest tests/test_x.py")
    got = _bundle(proj, bug)
    head = subprocess.run(
        ["git", "-C", str(proj), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    assert got["bug"] == bid and got["mode"] == "full"
    assert got["tree"]["ddflow_version"] == __version__
    assert got["tree"]["tree_sha"] == head == got["tree"]["main_sha"]
    assert got["tree"]["branch"] == "main"
    assert got["agent"]["id"] == "a1" and got["agent"]["derived_from"] == "explicit"
    assert got["env"]["DDFLOW_RUNNING_GATE"] == "ci"
    assert got["config_deltas"]["bugs.phase"]["layer"] != "default"
    assert got["item"]["id"] == "T1" and "gates" in got["item"]
    assert "load_avg" in got["machine"] and "disk_free_mb" in got["machine"]
    assert "unavailable" in got["commands"]  # no command log exists; the bundle says so
    assert got["repro_cmd"] == {"command": "uv run pytest tests/test_x.py", "executed": False}
    assert logblobs.problems(proj, EventLog(proj, "r").read_all()) == []


def test_secrets_in_env_and_config_are_redacted(proj, monkeypatch):
    monkeypatch.setenv("DDFLOW_API_KEY", "hunter2-not-for-logs")
    monkeypatch.setenv("DDFLOW_NOTE", f"deploy with {SECRET} please")
    monkeypatch.setenv("DDFLOW_EXTRA", "zebra-98765")
    _config(proj, f'[bugs]\nphase = "{SECRET}"\nrepro_redact_extra = ["zebra-\\\\d+"]\n')
    _, bug = _file(proj)
    got = _bundle(proj, bug)
    assert got["env"]["DDFLOW_API_KEY"] == "[set]"
    raw = (proj / ".ddflow" / "blobs").rglob("*.json")
    text = "".join(p.read_text() for p in raw)
    for leaked in ("hunter2-not-for-logs", SECRET, "zebra-98765"):
        assert leaked not in text, leaked
    assert "REDACTED" in got["env"]["DDFLOW_NOTE"]


def test_capture_off_stores_nothing(proj):
    _config(proj, '[bugs]\nrepro_capture = "off"\n')
    _, bug = _file(proj)
    assert bug.repro == {}
    assert not list((proj / ".ddflow" / "blobs").rglob("*.json"))


def test_the_call_can_override_the_config(proj):
    _config(proj, '[bugs]\nrepro_capture = "off"\n')
    _, bug = _file(proj, repro_capture="basic")
    got = _bundle(proj, bug)
    assert got["mode"] == "basic" and "item" not in got and "machine" not in got
    bad = K.bug_found(proj, summary="x", repro_capture="loud")
    assert not bad.ok and "repro_capture" in bad.reason


def test_the_cap_truncates(proj, monkeypatch):
    for i in range(60):
        monkeypatch.setenv(f"DDFLOW_BIG_{i}", "v" * 300)
    _config(proj, "[bugs]\nrepro_max_bytes = 2048\n")
    _, bug = _file(proj)
    assert bug.repro["truncated"] is True and bug.repro["bytes"] <= 2048
    got = _bundle(proj, bug)
    assert got["truncated"] is True and got["bug"] and len(B.body(got)) <= 2048


def test_the_blob_is_read_back_by_show_and_a_lost_blob_says_so(proj):
    bid, bug = _file(proj)
    shown = show(proj, bid)
    item = shown.data["item"]
    assert item["repro_bundle"]["bug"] == bid
    assert (
        "repro bundle (full" in item["repro_text"] and "ddflow " + __version__ in item["repro_text"]
    )
    path = proj / ".ddflow" / "blobs" / B.hexof(bug.repro["ref"])[:2]
    next(path.glob("*.json")).unlink()
    gone = RB.read(proj, bug.repro)
    assert not isinstance(gone, dict) and "unavailable" in gone
    assert logblobs.problems(proj, EventLog(proj, "r").read_all())


def test_test_id_runs_pytest_and_keeps_the_tail(proj):
    (proj / "test_probe.py").write_text("def test_probe():\n    assert 1 == 2, 'probe-marker'\n")
    _, bug = _file(proj, test="test_probe.py::test_probe")
    got = _bundle(proj, bug)["test"]
    assert got["exit_code"] == 1 and "probe-marker" in got["tail"]


def test_repro_cmd_is_recorded_never_run(proj):
    flag = proj / "ran.txt"
    _, bug = _file(proj, repro_cmd=f"touch {flag}")
    assert not flag.exists()
    assert _bundle(proj, bug)["repro_cmd"]["executed"] is False


def test_cli_flags_and_claiming_the_fix_task_prints_the_bundle(proj):
    code, out, err = run_cli(
        proj, "bug", "found", "--summary", "boom", "--item", "T1", "--id", "BX1",
        "--repro-cmd", "ddflow nope", "--repro-capture", "basic", agent="a1",
    )  # fmt: skip
    assert code == 0, err
    assert "repro bundle stored (basic" in out
    code, out, err = run_cli(proj, "show", "BX1")
    assert code == 0 and "repro bundle (basic" in out and "re-run: ddflow nope" in out
    code, out, _ = run_cli(proj, "--json", "show", "BX1")
    assert json.loads(out)["repro_bundle"]["repro_cmd"]["command"] == "ddflow nope"
    code, out, err = run_cli(proj, "claim", "fix-BX1", agent="a1")
    assert code == 0, err
    assert "Repro bundle of bug BX1" in out
