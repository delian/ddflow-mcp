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
    assert "repro bundle stored (basic)" in out
    code, out, err = run_cli(proj, "show", "BX1")
    assert code == 0 and "repro bundle (basic" in out and "re-run: ddflow nope" in out
    code, out, _ = run_cli(proj, "--json", "show", "BX1")
    assert json.loads(out)["repro_bundle"]["repro_cmd"]["command"] == "ddflow nope"
    code, out, err = run_cli(proj, "claim", "fix-BX1", agent="a1")
    assert code == 0, err
    assert "Repro bundle of bug BX1" in out


def test_an_unknown_mode_is_refused_not_degraded_to_basic(proj):
    from ddflow.config import Config

    log = EventLog(proj, "a1")
    with pytest.raises(ValueError, match="ful"):
        RB.build(proj, Config.load(proj), state(proj), log.read_all(), bug="B1", mode="ful")
    out = RB.attach(proj, Config.load(proj), state(proj), log, "B1", capture="ful")
    assert "unavailable" in out["repro"] and "ref" not in out["repro"]


def test_a_basic_bundle_does_not_point_at_a_section_it_lacks(proj):
    _, bug = _file(proj, repro_capture="basic")
    got = _bundle(proj, bug)
    assert "recent_events" not in got and "recent_events" not in got["commands"]["unavailable"]
    _, bug = _file(proj, repro_capture="full")
    assert "recent_events" in _bundle(proj, bug)


def _spy(monkeypatch):
    from ddflow.services import repro_facts as RF

    seen = []
    real = RF.P.capture
    monkeypatch.setattr(RF.P, "capture", lambda argv, **kw: seen.append(argv) or real(argv, **kw))
    return RF, seen


def test_a_test_id_that_looks_like_an_option_is_not_one(proj, monkeypatch):
    # The invariant, not one pytest's wording: nothing is run and the id is reported missing.
    # Python 3.12.3 (the GitHub runner) reads `pytest -- --co` as `--co`: exit 5, nothing found.
    _, seen = _spy(monkeypatch)
    _, bug = _file(proj, test="--co")
    got = _bundle(proj, bug)["test"]
    assert got["exit_code"] != 0 and "--co" in got["tail"] and "not found" in got["tail"]
    assert not seen  # a missing path never reaches pytest, whichever argparse it has


def test_a_dash_led_test_path_that_exists_is_never_a_bare_option(tmp_path, monkeypatch):
    RF, seen = _spy(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "-test_x.py").write_text("def test_ok():\n    pass\n")
    got = RF.test_run(tmp_path, "-test_x.py", 4000, 60)
    assert got["exit_code"] == 0, got["tail"]
    assert "-test_x.py" not in seen[0] and "./-test_x.py" in seen[0]


def test_a_bracket_in_the_file_name_is_part_of_the_path(tmp_path, monkeypatch):
    RF, _ = _spy(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "test_x[0].py").write_text("def test_ok():\n    pass\n")
    got = RF.test_run(tmp_path, "test_x[0].py::test_ok", 4000, 60)
    assert got["exit_code"] == 0, got["tail"]


def test_the_test_run_does_not_inherit_the_callers_pytest_options(tmp_path, monkeypatch):
    RF, _ = _spy(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "test_x.py").write_text("def test_ok():\n    pass\n")
    monkeypatch.setenv("PYTEST_ADDOPTS", "--co")  # would "collect" without running
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    got = RF.test_run(tmp_path, "test_x.py", 4000, 60)
    assert got["exit_code"] == 0 and "1 passed" in got["tail"], got["tail"]


def test_a_cwd_outside_the_project_is_not_recorded_whole(proj, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    _, bug = _file(proj)
    cwd = _bundle(proj, bug)["tree"]["cwd"]
    assert cwd == "<outside the project>/elsewhere" and str(tmp_path) not in cwd


def test_the_test_runs_where_the_bug_was_seen_not_at_the_project_root(proj, tmp_path, monkeypatch):
    here = tmp_path / "tree"
    here.mkdir()
    (here / "test_here.py").write_text("def test_here():\n    assert True\n")
    monkeypatch.chdir(here)
    _, bug = _file(proj, test="test_here.py::test_here")  # exists only in the cwd, not in proj
    got = _bundle(proj, bug)["test"]
    assert got["exit_code"] == 0 and "1 passed" in got["tail"]
