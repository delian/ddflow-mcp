"""Operational memory: the facts an agent must know before touching this machine.

Both projects ddflow was built to take over kept an OptMem store beside the repository,
and required every session to read it FIRST: "this box has 8 H200s", "use `-n 16`,
never `-n auto`", "a uniform-zero eval usually means a sidecar 404". The import turned it
into journal notes -- searchable, and never shown to anyone at session start, which is
the one thing the store existed for. These tests pin the replacement.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.surfaces.mcp import Server

OK, FAIL, NOTHING = 0, 1, 2


def _state(repo: Path):
    return fold(EventLog(repo).read_all(), strict=False)


def _mcp(srv: Server, name: str, **args) -> dict:
    reply = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        }
    )
    assert reply is not None
    return reply["result"]


def test_a_memory_is_recorded_listed_and_forgotten_with_a_reason(repo):
    run_cli(repo, "init")
    code, out, err = run_cli(
        repo, "--json", "memory", "add", "8x H200 on this box", "--tags", "gpu"
    )
    assert code == OK, err
    mid = json.loads(out)["id"]
    code, out, _ = run_cli(repo, "--json", "memory", "list")
    rows = json.loads(out)["memories"]
    assert [r["text"] for r in rows] == ["8x H200 on this box"]
    assert rows[0]["tags"] == ["gpu"]

    code, _out, _err = run_cli(repo, "memory", "forget", mid, "--reason", "")
    assert code == FAIL, "forgetting without saying why must be refused"
    code, _out, err = run_cli(repo, "memory", "forget", mid, "--reason", "moved to a new box")
    assert code == OK, err
    code, out, _ = run_cli(repo, "--json", "memory", "list")
    assert code == NOTHING, "a forgotten memory is not a live one"
    code, out, _ = run_cli(repo, "--json", "memory", "list", "--all")
    assert json.loads(out)["memories"][0]["forgotten"] == "moved to a new box"


def test_a_paragraph_is_refused_not_truncated(repo):
    """A memory cut mid-sentence says something its author did not."""
    run_cli(repo, "init")
    code, out, _ = run_cli(repo, "memory", "add", "x" * 281)
    assert code == FAIL
    assert "max_chars" in out
    assert not _state(repo).memories, "a refused memory was written anyway"


def test_brief_shows_memories_newest_first_and_says_when_it_is_partial(repo):
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text("[memory]\nbrief_items = 2\n")
    for text in ("oldest fact", "middle fact", "newest fact"):
        run_cli(repo, "memory", "add", text)
    code, out, err = run_cli(repo, "brief")
    assert code == OK, err
    section = out.split("## Operational memory", 1)[1]
    assert section.index("newest fact") < section.index("middle fact")
    assert "oldest fact" not in section.split("more:")[0]
    assert "1 more" in section


def test_recall_finds_a_memory_and_not_a_forgotten_one(repo):
    run_cli(repo, "init")
    _c, out, _e = run_cli(repo, "--json", "memory", "add", "sidecar returns 404 when vllm restarts")
    live = json.loads(out)["id"]
    run_cli(repo, "memory", "add", "stale fact about the sidecar 404", "--id", "M-old")
    run_cli(repo, "memory", "forget", "M-old", "--reason", "fixed upstream")
    code, out, err = run_cli(repo, "--json", "recall", "sidecar 404")
    assert code == OK, err
    hits = json.loads(out).get("memories", [])
    assert [h["id"] for h in hits] == [live], hits
    assert hits[0]["kind"] == "MEMORY"


def test_re_recording_by_id_CORRECTS_a_memory(repo):
    run_cli(repo, "init")
    run_cli(repo, "memory", "add", "GPUs 0-3 are free", "--id", "M-gpu")
    _code, out, _ = run_cli(repo, "--json", "memory", "add", "GPUs 4-7 are free", "--id", "M-gpu")
    assert json.loads(out)["replaced"] is True
    assert _state(repo).memories["M-gpu"].text == "GPUs 4-7 are free"


def test_over_mcp_a_subagent_is_the_author_of_its_memory(repo):
    run_cli(repo, "init")
    srv = Server(repo)
    res = _mcp(srv, "ddflow_memory_add", text="suite takes 24 minutes", as_agent="sub-7")
    assert not res.get("isError"), res
    m = next(iter(_state(repo).memories.values()))
    assert m.by == "sub-7"
    listed = json.loads(_mcp(srv, "ddflow_memory_list")["content"][0]["text"])
    assert listed["memories"][0]["text"] == "suite takes 24 minutes"


OPTMEM = (
    "#0 2026-07-31 run_nemo_run: LLM distillation pipeline; 8x H200 GPUs on this box       \n"
    "#1 2026-08-02 Full suite: uv run pytest -q -n 16, never -n auto                       \n"
)


def test_an_optmem_store_imports_as_MEMORIES_with_the_date_they_became_true(repo):
    (repo / ".agent_memory").mkdir()
    (repo / ".agent_memory" / "LOG.txt").write_text(OPTMEM)
    code, _out, err = run_cli(repo, "import", "--apply")
    assert code == OK, err
    st = _state(repo)
    assert set(st.memories) == {"M-0000", "M-0001"}
    assert st.memories["M-0000"].origin_at == "2026-07-31"
    assert "8x H200" in st.memories["M-0000"].text
    assert st.memories["M-0001"].source.startswith(".agent_memory/LOG.txt")
    assert "s-imported-memory" not in st.sessions, "memories landed as journal notes again"

    code, out, _ = run_cli(repo, "brief")
    assert "8x H200" in out, "an imported memory must reach the session-start brief"

    code, _out, _err = run_cli(repo, "import", "--apply")
    assert code == NOTHING, "a second import re-remembered the same facts"
    code, out, _ = run_cli(repo, "--json", "import", "--verify")
    assert json.loads(out)["imported"].get("memory") == 2


def test_all_includes_forgotten_memories_with_or_without_a_query(repo):
    """roborev 825: the index holds live memories only, so `--query X --all` silently
    dropped the forgotten ones -- 'was this ever a fact?' answered 'no'."""
    run_cli(repo, "init")
    run_cli(repo, "memory", "add", "sidecar 404 means vllm restarted", "--id", "M-old")
    run_cli(repo, "memory", "forget", "M-old", "--reason", "fixed upstream")
    run_cli(repo, "memory", "add", "sidecar logs live under vllm-logs/")
    for extra in ((), ("--query", "sidecar")):
        _c, out, _e = run_cli(repo, "--json", "memory", "list", "--all", *extra)
        ids = {m["id"] for m in json.loads(out)["memories"]}
        assert "M-old" in ids, f"forgotten memory missing with {extra or 'no query'}"


def test_a_query_returns_every_match_not_the_first_twenty(repo):
    run_cli(repo, "init")
    for i in range(23):
        run_cli(repo, "memory", "add", f"gpu fact number {i}", "--id", f"M-{i}")
    _c, out, _e = run_cli(repo, "--json", "memory", "list", "--query", "gpu")
    assert len(json.loads(out)["memories"]) == 23
