"""`--dry-run` for `ddflow hooks run` and its MCP twin (Bbf3e91d5f1).

Probing `hooks run` computes and shapes the hook's reply, but the probe also RECORDS:
running the prompt event appended session.started / session.prompt to the committed
event log. `--dry-run` keeps the shaped reply and writes no event -- the shards before
and after a dry run are byte-identical.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli  # noqa: E402

from ddflow.api import setup as A  # noqa: E402

#: Claude's dialect; the same payload drives the counterfactual (recording) probes.
PROMPT = {
    "session_id": "dry-run-probe",
    "prompt": "probe the prompt hook",
    "source": "startup",
    "model": "t",
}


def _run(repo: Path, *argv: str, stdin: str = "") -> tuple[int, str, str]:
    root = str(Path(__file__).resolve().parents[1])
    p = subprocess.run(
        [sys.executable, "-m", "ddflow", "--repo", str(repo), *argv],
        input=stdin,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": root},
        cwd=repo,
        timeout=120,
    )
    return p.returncode, p.stdout, p.stderr


def _shards(repo: Path) -> dict[str, bytes]:
    """The committed event log as `{shard name: bytes}`: byte-identical means nothing was
    appended, rewritten or added."""
    return {
        shard.name: shard.read_bytes()
        for shard in sorted((repo / ".ddflow" / "events").glob("*.jsonl"))
    }


def _prompt_texts(repo: Path) -> list[str]:
    out = []
    for shard in (repo / ".ddflow" / "events").glob("*.jsonl"):
        for line in shard.read_text().splitlines():
            ev = json.loads(line)
            if ev["kind"] == "session.prompt":
                out.append(ev["data"]["text"])
    return out


def test_dry_run_is_offered_by_the_run_parser(repo):
    _code, out, _err = run_cli(repo, "hooks", "run", "--help")
    assert "--dry-run" in out


def test_dry_run_prompt_writes_no_event(repo):
    run_cli(repo, "init")
    before = _shards(repo)
    payload = json.dumps(PROMPT)
    for _ in range(2):  # a probe run twice still writes nothing
        code, out, err = _run(
            repo, "hooks", "run", "prompt", "--harness", "claude", "--dry-run", stdin=payload
        )
        assert code == 0, err
        assert out == ""  # the reply is shaped as on a real run
    assert _shards(repo) == before


def test_without_dry_run_the_same_probe_records(repo):
    """The counterfactual that pins what --dry-run suppresses: the un-flagged run DOES
    append session.prompt, and a dry run after it still changes nothing."""
    run_cli(repo, "init")
    payload = json.dumps(PROMPT)
    code, _out, err = _run(repo, "hooks", "run", "prompt", "--harness", "claude", stdin=payload)
    assert code == 0, err
    assert PROMPT["prompt"] in _prompt_texts(repo)
    after_record = _shards(repo)
    code, _out, err = _run(
        repo, "hooks", "run", "prompt", "--harness", "claude", "--dry-run", stdin=payload
    )
    assert code == 0, err
    assert _shards(repo) == after_record


def test_dry_run_session_start_still_prints_the_brief_and_writes_no_event(repo):
    """session_start is the handler whose real run SYNCS ([schedule] repos observations
    append external.observed on a change), so its dry run must skip the syncing sections
    (the committed log stays untouched) while still printing the brief."""
    run_cli(repo, "init")
    before = _shards(repo)
    code, out, err = _run(
        repo, "hooks", "run", "session_start", "--harness", "claude", "--dry-run", stdin="{}"
    )
    assert code == 0, err
    assert out.startswith("# ddflow session start")
    assert _shards(repo) == before


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(path), "config", k, v], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "--allow-empty", "-m", "i"], check=True)
    return path


def test_dry_run_session_start_does_not_record_external_observations(repo):
    """Non-vacuous: with a live external need CONFIGURED, a real session start's sync
    records external.observed; --dry-run must still write nothing (test_external_deps.py
    is the pair's model)."""
    trainer = _git_repo(repo.parent / "trainer")
    run_cli(trainer, "init")
    run_cli(trainer, "task", "add", "132.D", "--title", "train-record type", "--globs", "t.py")
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text('[schedule]\nrepos = ["trainer=../trainer"]\n')
    run_cli(repo, "task", "add", "GEN.1", "--needs", "trainer:132.D", "--globs", "g.py")
    assert run_cli(repo, "--json", "external", "sync")[0] == 0
    before = _shards(repo)
    code, out, err = _run(
        repo, "hooks", "run", "session_start", "--harness", "claude", "--dry-run", stdin="{}"
    )
    assert code == 0, err
    assert out.startswith("# ddflow session start")
    assert _shards(repo) == before
    # The drain: the dry probe was AFTER the initial observation, which a real session
    # start on an unconfigured pair would have written inside the dry run instead.


def test_dry_run_session_start_never_runs_the_brief_sweep(repo, monkeypatch):
    """The brief's sweep housekeeping is a WRITE (worktree.removed events, leftover trees
    removed), so a dry run must not reach it; a spy pins that the dry brief build skips it."""
    from ddflow.services import cleanup as CL

    run_cli(repo, "init")
    before = _shards(repo)
    seen: list = []

    def spy(*args, **kwargs):
        seen.append(True)
        return CL.sweep_finished(*args, **kwargs)

    monkeypatch.setattr(CL, "sweep_finished", spy)
    out = A.hooks(
        repo, action="run", event="session_start", harness="claude", stdin="{}", dry_run=True
    )
    assert out.exit == 0
    assert "# ddflow session start" in out.data["stdout"]
    assert seen == []  # the sweeping write path was never entered
    assert _shards(repo) == before


def test_json_run_still_exits_zero_and_reports_skips(repo):
    """The --json reply reads result/dry_run with .get: the skip paths (unknown event,
    unknown harness) leave them out, and indexing would raise. A hook must always exit 0."""
    run_cli(repo, "init")
    code, out, err = _run(
        repo, "hooks", "run", "bogus", "--harness", "claude", "--json", stdin="{}"
    )
    assert code == 0, err
    reply = json.loads(out)
    assert "unknown event 'bogus'" in reply["note"]
    assert "dry_run" in reply
    assert code == _run(
        repo, "hooks", "run", "bogus", "--harness", "claude", "--json", "--dry-run", stdin="{}"
    )[0] == 0


def test_the_api_route_the_mcp_twin_takes_also_writes_nothing_on_dry_run(repo):
    """Over MCP the same work is `ddflow_hooks` reaching `api.hooks(action="run", ...)`;
    its `dry_run` argument must behave exactly as the CLI flag."""
    run_cli(repo, "init")
    before = _shards(repo)
    out = A.hooks(
        repo,
        action="run",
        event="prompt",
        harness="claude",
        stdin=json.dumps(PROMPT),
        dry_run=True,
    )
    assert out.exit == 0, out.reason
    assert out.data.get("dry_run") is True
    assert _shards(repo) == before
