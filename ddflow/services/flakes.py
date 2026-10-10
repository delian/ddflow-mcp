"""What a failing test run leaves behind, and the flake log (B-uc-gate-failure-evidence).

A gate that fails keeps only a summary line, so nobody can see WHICH test failed or why, and
a test that failed once under load and passed on the rerun leaves no trace at all. This
module is the one home for both halves:

* `failed_tests` / `failure_tails` read a runner's output for the failing test ids and the
  last lines of each failure (pytest's short summary and `___ name ___` sections, unittest's
  `FAIL:` / `ERROR:` headings), capped; `failure_evidence` is the evidence fragment a gate
  keeps. The event log redacts free text at append (core.redact); the flake log masks the configured
  secret patterns itself.
* The flake log, `.ddflow/local/flakes.jsonl` -- machine-local, never committed: one line
  per test that FAILED on a tree and PASSED on the same tree (a rerun of the failed tests,
  or the gate run again on identical content), with the commit, the host load and the
  failure text. `ddflow tests --flakes` shows it; `doctor` names a test that failed 3+ times.

A flake is a record of something that happened, not a pass: a gate that failed is re-run
through ddflow until it passes (decision D-failed-gate-rerun).
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

from ..config import Config
from ..core import clock
from ..infra import fsio
from .redact_report import redact_secrets

#: The machine-local log, relative to the primary checkout.
FLAKE_LOG = Path(".ddflow") / "local" / "flakes.jsonl"
#: A test that failed this many times is named by `doctor`.
CHRONIC_FAILS = 3
#: Failing ids kept in one gate's evidence (the rest are counted).
MAX_FAILED_TESTS = 50
#: Failure sections kept, the last lines of each, and the caps on a line and a section.
MAX_TAILS = 10
TAIL_LINES = 12
LINE_CHARS = 240
TAIL_CHARS = 1500
#: Flake rows kept in the file; older ones are dropped when it is rewritten.
MAX_ROWS = 500

# pytest's short test summary: `FAILED tests/x.py::test_y[a-b] - AssertionError: boom`. The
# id runs to the first ` - ` (a parameter id may hold spaces: `test_y[hello world]`).
_PYTEST_LINE = re.compile(
    r"^(?:FAILED|ERROR)\s+(?P<id>\S+\.py(?:::.+?)?|\S+::.+?)(?:\s+-\s+(?P<why>.*))?$"
)
# unittest: `FAIL: test_y (pkg.mod.Class.test_y)` / `ERROR: test_y (pkg.mod.Class)`.
_UNITTEST_LINE = re.compile(r"^(?:FAIL|ERROR):\s+(?P<name>\w+)\s+\((?P<where>[\w.]+)\)\s*$")
# pytest failure section header: `____ test_y ____` / `____ TestC.test_y[a-b] ____`.
_SECTION = re.compile(r"^_{3,}\s+(?P<name>.+?)\s+_{3,}$")
_BANNER = re.compile(r"^={3,}")


def failed_tests(text: str) -> list[dict[str, str]]:
    """``[{"id", "why"}]`` for every failing test the output names, once each, in order."""
    seen: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if m := _PYTEST_LINE.match(line):
            seen.setdefault(m["id"], (m["why"] or "")[:LINE_CHARS])
        elif m := _UNITTEST_LINE.match(line):
            where = m["where"]
            tid = where if where.endswith(m["name"]) else f"{where}.{m['name']}"
            seen.setdefault(tid, "")
    return [{"id": k, "why": v} for k, v in seen.items()]


def failure_tails(text: str) -> list[dict[str, str]]:
    """``[{"test", "tail"}]``: the last lines of each failure section, capped."""
    tails: list[dict[str, str]] = []
    name: str | None = None
    body: list[str] = []

    def close() -> None:
        if name is not None and len(tails) < MAX_TAILS:
            lines = [ln[:LINE_CHARS] for ln in body if ln.strip()][-TAIL_LINES:]
            tails.append({"test": name, "tail": "\n".join(lines)[-TAIL_CHARS:]})

    for raw in text.splitlines():
        line = raw.rstrip()
        if m := _SECTION.match(line.strip()):
            close()
            name, body = m["name"], []
        elif _BANNER.match(line.strip()):
            close()
            name, body = None, []
        elif name is not None:
            body.append(line)
    close()
    return tails


def failure_evidence(text: str) -> dict[str, Any]:
    """The evidence a gate keeps about the tests that failed in ``text`` ({} when none)."""
    found = failed_tests(text)
    if not found:
        return {}
    ev: dict[str, Any] = {
        "failed_tests": [f["id"] for f in found[:MAX_FAILED_TESTS]],
        "failed_reasons": {f["id"]: f["why"] for f in found[:MAX_FAILED_TESTS] if f["why"]},
    }
    if len(found) > MAX_FAILED_TESTS:
        ev["failed_tests_more"] = len(found) - MAX_FAILED_TESTS
    if tails := failure_tails(text):
        ev["failure_tails"] = tails
    if not ev["failed_reasons"]:
        del ev["failed_reasons"]
    return ev


def failure_text(ev: dict[str, Any]) -> str:
    """The failing tests of a gate's evidence, as lines for a person ("" when none)."""
    ids = ev.get("failed_tests") or []
    if not ids:
        return ""
    why = ev.get("failed_reasons") or {}
    lines = [f"{len(ids) + int(ev.get('failed_tests_more') or 0)} failing test(s):"]
    lines += [f"  FAILED {i}" + (f" - {why[i]}" if i in why else "") for i in ids]
    if ev.get("failed_tests_more"):
        lines.append(f"  ... and {ev['failed_tests_more']} more")
    for t in ev.get("failure_tails") or []:
        lines.append(f"--- {t['test']} ---")
        lines.append(t["tail"])
    rr = ev.get("rerun")
    if rr:
        lines.append(
            f"rerun of the failed tests once: {rr.get('outcome', '?')}"
            + (f" ({rr['note']})" if rr.get("note") else "")
        )
    return "\n".join(lines)


# -- the flake log ---------------------------------------------------------------------


def log_path(repo: Path) -> Path:
    return Path(repo) / FLAKE_LOG


def load_average() -> float | None:
    """The 1-minute load average per CPU, or None where the platform has none."""
    try:
        return round(os.getloadavg()[0] / (os.cpu_count() or 1), 2)
    except (AttributeError, OSError):
        return None


def read(repo: Path) -> list[dict[str, Any]]:
    """Every readable row of the log, oldest first; a damaged line is skipped. Raises OSError
    when the file exists and cannot be read."""
    try:
        text = log_path(repo).read_text("utf-8", errors="replace")
    except FileNotFoundError:
        return []  # no log yet; any other OSError is "could not read", not "no flakes"
    rows = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("test"):
            rows.append(row)
    return rows


def record(
    repo: Path,
    tests: list[str],
    *,
    item: str = "",
    gate: str = "",
    commit: str = "",
    how: str = "",
    why: dict[str, str] | None = None,
    tails: list[dict[str, str]] | None = None,
    cfg: Config | None = None,
) -> int | None:
    """Append one row per test that failed and then passed on the same tree; returns how
    many were written, or None when the log could not be written. Never raises: a log that
    cannot be written must not change a gate."""
    if not tests:
        return 0
    at = clock.now_iso()
    by_name = {t["test"]: t["tail"] for t in tails or []}

    def tail_of(test: str) -> str:
        """The section whose header names exactly this test (`Class.test[p]` for
        `f.py::Class::test[p]`) -- never one whose name merely contains or extends it."""
        return next((v for k, v in by_name.items() if _names(k, test)), "")

    load = load_average()
    lines = []
    for t in tests:
        row = {
            "test": _clean(t, cfg),
            "at": at,
            "item": item,
            "gate": gate,
            "commit": commit,
            "load": load,
            "how": how,
            "failure": _clean((why or {}).get(t, "") or tail_of(t), cfg),
        }
        lines.append(json.dumps(row, sort_keys=True) + "\n")
    path = log_path(repo)
    try:
        fsio.ensure_ignored_dir(path.parent, comment="machine-local state: never committed")
        # One lock for the append and the trim: lanes on one machine write this file at
        # once, and a trim that read before another's append would drop that row.
        with fsio.file_lock(path.with_name(path.name + ".lock"), timeout_s=5):
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
            try:
                data = "".join(lines).encode("utf-8")
                while data:
                    data = data[os.write(fd, data) :]
            finally:
                os.close(fd)
            _trim(path)
    except (OSError, fsio.LockTimeout):
        return None
    return len(lines)


def _names(header: str, test: str) -> bool:
    """Whether a failure-section header (`TestC.test_b[x-1]`) names the pytest id ``test``."""
    base, bracket, param = header.partition("[")
    key = base.replace(".", "::") + bracket + param
    # Compared with the id's node path (what follows the file), so a bare `test_b` header
    # never names `f.py::TestC::test_b`.
    node = test.partition("::")[2] if "::" in test else test
    return bool(base) and node == key


def _clean(text: str, cfg: Config | None) -> str:
    """``text`` with the configured secret patterns masked (the log redacts the same way)."""
    return redact_secrets(text, cfg)[0] if text else ""


def _trim(path: Path) -> None:
    """Keep the newest MAX_ROWS lines once the file holds twice that (a rewrite, atomic)."""
    try:
        lines = path.read_text("utf-8", errors="replace").splitlines(keepends=True)
        if len(lines) > 2 * MAX_ROWS:
            fsio.atomic_write(path, "".join(lines[-MAX_ROWS:]), fsync=False)
    except OSError:
        pass


def counts(rows: list[dict[str, Any]]) -> Counter[str]:
    return Counter(r["test"] for r in rows)


def flakes_text(repo: Path, limit: int = 30) -> str:
    """The flake log as `ddflow tests --flakes` prints it."""
    try:
        rows = read(repo)
    except OSError as exc:
        return f"Could not read the flake log {FLAKE_LOG.as_posix()}: {exc}"
    if not rows:
        return (
            f"No flakes logged ({FLAKE_LOG.as_posix()} is empty or missing): no test has failed "
            "and then passed on the same tree through ddflow on this machine."
        )
    lines = [f"{len(rows)} flake(s) logged, by test (most often first):"]
    for test, n in counts(rows).most_common():
        lines.append(f"  {n}x  {test}")
    lines.append("\nlatest:")
    for r in rows[-limit:]:
        load = f" load {r['load']}" if r.get("load") is not None else ""
        lines.append(
            f"  {r.get('at', '')[:19]}  {r['test']}  [{r.get('gate', '')} {r.get('item', '')}"
            f" @{str(r.get('commit', ''))[:10]}{load}; {r.get('how', '')}]"
        )
        if r.get("failure"):
            lines.append(f"      {str(r['failure']).splitlines()[-1][:LINE_CHARS]}")
    return "\n".join(lines)


def doctor_notes(repo: Path) -> list[str]:
    """A note for each test that has flaked CHRONIC_FAILS or more times."""
    try:
        rows = read(repo)
    except OSError as exc:
        return [
            f"the flake log {FLAKE_LOG.as_posix()} cannot be read ({exc}): flaky tests are not being counted"
        ]
    return [
        f"flaky test: {test} failed then passed {n} times on one tree "
        f"(`ddflow tests --flakes`) -- fix it or quarantine it"
        for test, n in counts(rows).most_common()
        if n >= CHRONIC_FAILS
    ]
