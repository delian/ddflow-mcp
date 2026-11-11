""".ddflow/local/cmdlog.jsonl — the last N ddflow CLI commands (DX-command-log).

DX-bug-repro-bundle found ddflow keeps no trace of the commands a session ran: the repro
bundle listed the agent's log events *instead*, and said so. A bounded, redacted,
git-ignored ring log under `.ddflow/local/`, written by the CLI entry point, gives "the
last N ddflow commands with exit codes and output tails".

Each record is one JSON line:

    {"t": <unix seconds>, "agent": "...", "argv": [<shape>], "exit": <int>, "tail": "..."}

``argv`` is the *shape* of the command line (``core.redact.redact_argv``): the program and
the CLI's own verbs and flag names are kept, every other token is a placeholder, so a value
typed with a secret in it never lands in the file. ``tail`` is the command's own output tail,
redacted by the ``log`` profile (the machine's host, home and the repo root are removed; the
~/.ddflow/.gitignore ``local/`` line keeps the file itself out of git). The file is bounded:
past ``CAP_ENTRIES`` lines the oldest are dropped. Writing and reading never raise — a full or
read-only log must not break the command being logged.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable
from pathlib import Path

from ..core.redact import redact_argv
from .redact_report import redactor

CAP_ENTRIES = 500  #: The ring size (a module constant for this slice; the knob is for later).
TAIL_CHARS = 4000  #: How many characters of one command's output are kept per record.
REL = ".ddflow/local/cmdlog.jsonl"  #: relative to a repo root, in the git-ignored layer


def _path(repo: Path) -> Path:
    return Path(repo) / REL


def record(
    repo: Path,
    agent: str,
    argv: Iterable[str],
    code: int | None,
    tail: str,
    *,
    verbs: Iterable[str] = (),
    flags: Iterable[str] = (),
) -> None:
    """Append one record; never raises. ``argv`` is reduced to its shape and ``tail`` is
    redacted in place (the ``log`` profile without the config's secret patterns, so the
    machine's host/home/repo paths a traceback frames are removed)."""

    path = _path(repo)
    try:
        line = {
            "t": time.time(),
            "agent": agent or os.environ.get("DDFLOW_AGENT", ""),
            "argv": redact_argv(list(argv), verbs, flags),
            "exit": int(code) if code is not None else None,
            "tail": redactor("log").text(tail or "").text[-TAIL_CHARS:],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, default=str) + "\n")
    except (OSError, ValueError, TypeError):
        return
    _trim(path)


def _trim(path: Path) -> None:
    """Drop the oldest lines once the log has grown past ``CAP_ENTRIES``. The read-then-rewrite
    is not atomic across two processes (the CLI and the post_tool hook may write at once): a
    record appended by the other writer during the rewrite can be dropped, and `recent` skips a
    line torn by it. That is accepted -- this is a best-effort "last N" ring, not a durable log."""
    try:
        if path.stat().st_size == 0:
            return
        text = path.read_text(encoding="utf-8")
        if text.count("\n") <= CAP_ENTRIES:
            return
        path.write_text("".join(text.splitlines(keepends=True)[-CAP_ENTRIES:]), encoding="utf-8")
    except OSError:
        return


def recent(repo: Path, n: int) -> list[dict]:
    """The last ``n`` records, oldest first; an empty list when there is none (or ``n <= 0``:
    asking for none of the log is none, never the whole ring). Never raises."""
    if n <= 0:
        return []
    try:
        path = _path(repo)
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()[-n:]
    except OSError:
        return []
    out: list[dict] = []
    for line in lines:
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out
