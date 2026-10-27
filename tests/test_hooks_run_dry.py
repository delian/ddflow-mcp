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
