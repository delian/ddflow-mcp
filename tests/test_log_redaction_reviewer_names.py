"""Local reviewer service and model names never reach the committed log (B8ef0d325ed).

A reviewer's endpoint, model name and key variable belong in the git-ignored
``.ddflow/local/`` layer. Its service name (and its model string) is none of a secret,
a host, a path or the machine's own name, so the committed-log redactor does not
recognise it and it leaks into the pushed shards as review evidence. Every value here
is synthetic.
"""

from __future__ import annotations

from pathlib import Path

from ddflow.infra.log import EventLog

NAME = "local-reviewer-f8f8"  # a service name only the ignored reviewers.toml holds
MODEL = "Local/Reviewer-Flash"  # its model string


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".ddflow").mkdir(parents=True)
    (root / ".ddflow" / "reviewers.toml").write_text(
        "[[reviewer]]\n"
        f'name = "{NAME}"\n'
        'kind = "openai"\n'
        'base_url = "http://127.0.0.1:8000/v1"\n'
        f'model = "{MODEL}"\n'
    )
    return root


def _shard(root: Path) -> str:
    return "".join(p.read_text() for p in (root / ".ddflow" / "events").glob("*.jsonl"))


def test_a_local_reviewer_name_never_reaches_the_shard(tmp_path):
    root = _root(tmp_path)
    log = EventLog(root, "T")
    log.stamp = False
    log.append(
        "gate.failed",
        "T",
        {
            "gate": "rubber_duck",
            "reason": "",
            "evidence": {"reviewer": NAME, "model": MODEL, "family": "local"},
        },
    )
    shard = _shard(root)
    assert NAME not in shard, f"the reviewer service name {NAME!r} reached the committed log"
    assert MODEL not in shard, f"the reviewer model {MODEL!r} reached the committed log"
    assert "[REDACTED:name]" in shard
