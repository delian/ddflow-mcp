"""Helpers the test modules share: the git runner, small log/state/config builders.

Each was a private copy in several test files; a file imports the one it used
under its old name (`from helpers import git as _git`), so its call sites stay as they were.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ddflow.config import Config
from ddflow.core.events import Event
from ddflow.core.model import fold
from ddflow.infra.log import EventLog

if TYPE_CHECKING:  # the surfaces load only for the helpers that need them
    from ddflow.surfaces.mcp import Server


def flow_config(**flow) -> Config:
    c = Config()
    for k, v in flow.items():
        setattr(c.flow, k, v)
    return c


def git(where: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(where), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def git_proc(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=180
    )


def git_quiet(where, *args) -> None:
    subprocess.run(["git", "-C", str(where), *args], check=True, capture_output=True)


def git_raw(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def item_of(repo: Path, item: str = "T1"):
    return fold(EventLog(repo).read_all(), strict=False).items[item]


def items_of(repo: Path):
    return fold(EventLog(repo, "probe").read_all(), strict=False).items


def make_event(kind: str, subject: str, lamport: int, agent: str = "x", **data) -> Event:
    e = Event(kind=kind, subject=subject, data=data, agent=agent, lamport=lamport, ts=f"t{lamport}")
    return Event(**{**e.__dict__, "id": e.compute_id()})


def parse_cli(*argv):
    from ddflow.surfaces.cli import build_parser

    return build_parser().parse_args(list(argv))


def rpc_call(srv: Server, name: str, **args) -> dict:
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


def state(repo):
    return fold(EventLog(repo).read_all(), strict=False)


def state_as_reader(repo: Path):
    return fold(EventLog(repo, "reader").read_all(), strict=False)


def tool_json(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


def upgrade_plan(repo: Path) -> dict[str, Any]:
    from ddflow.api._base import _load
    from ddflow.services import upgrade_plan as UP

    log, cfg, st = _load(repo, "upgrader")
    return UP.build(repo, log, cfg, st)


def write_file(repo: Path, rel: str, text: str) -> Path:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def write_text_at(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def commit(repo, files, msg):
    for name, text in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", msg)
    return git(repo, "rev-parse", "HEAD")
