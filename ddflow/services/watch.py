"""`watch`: one call that waits for a job, a log line, a process or a gate, keeps the caller's
leases alive meanwhile, and says what it saw (DX-watch-job).

Agents wrote this by hand every long run: an until-loop, a ``pgrep`` that matched its own
shell, a heartbeat script. The probes here are small and pure; the loop and the renewal are
in `ddflow.api.watch`. A wait is bounded and RESUMABLE: a timeout hands back a token that
carries the target and, for a file, the byte offset already read, so the next call goes on
where this one stopped and no line is read twice or skipped.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.digest import content_digest
from . import jobs as J

KINDS = ("job", "file", "pid", "gate")


@dataclass
class Target:
    """What is watched. Exactly one of ``job``, ``file``, ``pid`` or ``gate`` is set."""

    job: str = ""
    file: str = ""
    until: str = ""
    pid: int = 0
    gate: str = ""  # "ITEM:GATE"
    offset: int = 0  # file: bytes already read
    sig: str = ""  # file: digest of the first bytes read, to tell a rewrite from an append
    start: str = ""  # pid: its start time when first seen
    heartbeat: list[str] = field(default_factory=list)

    @property
    def kind(self) -> str:
        return next((k for k in KINDS if getattr(self, k)), "")


def encode(t: Target) -> str:
    """The resumable token: the whole target, urlsafe."""
    raw = json.dumps(t.__dict__, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode(token: str) -> Target | None:
    """The target a token names, or None if it is not one of ours."""
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        d = json.loads(raw)
        if not isinstance(d, dict):
            return None
        known = Target.__dataclass_fields__
        t = Target(**{k: v for k, v in d.items() if k in known})
        ints = (t.offset, t.pid)
        strs = (t.job, t.file, t.until, t.gate, t.sig, t.start)
        if not (
            all(type(i) is int and i >= 0 for i in ints)
            and all(isinstance(x, str) for x in strs)
            and isinstance(t.heartbeat, list)
            and all(isinstance(h, str) for h in t.heartbeat)
        ):
            return None
        return t
    except (ValueError, TypeError, AttributeError):
        return None


def problem(t: Target) -> str:
    """Why this target cannot be watched, or ''."""
    named = [k for k in KINDS if getattr(t, k)]
    if len(named) != 1:
        return "name exactly one of --job, --file (with --until), --pid or --gate ITEM:GATE"
    if t.file and not t.until:
        return "--file needs --until REGEX: the line to wait for"
    if t.until and not t.file:
        return "--until belongs with --file"
    if t.until:
        try:
            re.compile(t.until)
        except re.error as exc:
            return f"--until is not a regular expression: {exc}"
    if t.pid < 0:
        return "--pid must be a process id"
    if t.gate and (":" not in t.gate or not all(t.gate.partition(":")[::2])):
        return "--gate takes ITEM:GATE"
    return ""


def _sig(data: bytes, upto: int) -> str:
    """A digest of the first (at most 64) bytes of ``data[:upto]``."""
    return content_digest(data[: min(64, upto)], length=12)


def scan_file(t: Target) -> str | None:
    """The first COMPLETE line past ``t.offset`` matching ``t.until`` (None: no match yet).

    ``t.offset`` moves past what was read: past the matching line, or past every complete
    line scanned. A half-written last line is left for the next pass, so a pattern is never
    judged against a line that is still being written. A file that shrank was rewritten:
    it is read again from the start. (Header and size are all a rewrite is judged by: one that keeps
    the first 64 bytes and is at least as long cannot be told from an append.)"""
    try:
        data = Path(t.file).read_bytes()
    except OSError:
        return None
    # A shrunk file, or one whose first bytes are no longer the ones read, was rewritten.
    if len(data) < t.offset or (t.offset and _sig(data, t.offset) != t.sig):
        t.offset = 0
    pat = re.compile(t.until)
    pos = t.offset
    while True:
        end = data.find(b"\n", pos)
        if end < 0:
            break
        line = data[pos:end].decode("utf-8", "replace").rstrip("\r")
        pos = end + 1
        if pat.search(line):
            t.offset, t.sig = pos, _sig(data, pos)
            return line
    t.offset, t.sig = pos, _sig(data, pos)
    return None


def pid_gone(t: Target) -> bool:
    """The process is over, or the pid now belongs to another process."""
    if not t.start:
        t.start = J.proc_start(t.pid)
    if not J.alive(t.pid):
        return True
    return bool(t.start) and J.proc_start(t.pid) not in ("", t.start)


def gate_outcome(st, t: Target) -> str:
    """The outcome recorded for the watched gate ('' while none is)."""
    item, _, gate = t.gate.partition(":")
    it = st.items.get(item)
    rec = it.gates.get(gate) if it is not None else None
    return (rec.outcome or "") if rec is not None else ""


def summary(t: Target, seen: dict[str, Any]) -> str:
    """One line for a human: what ended the wait."""
    kind = t.kind
    if kind == "file":
        return seen.get("line", "")
    if kind == "job":
        return f"job {t.job}: {seen.get('detail', '')}"
    if kind == "pid":
        return f"pid {t.pid} is gone"
    return f"gate {t.gate}: {seen.get('outcome', '')}"
