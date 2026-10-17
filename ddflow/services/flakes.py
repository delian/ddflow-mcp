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
# A long test name leaves room for one underscore a side: `_ ERROR at setup of test_x _`.
# Read only inside the ERRORS / FAILURES block, and only unindented (Bb9ce40cfcc).
_SHORT_SECTION = re.compile(r"^_{1,2}\s+(?P<name>\S.*?)\s+_{1,3}$")
_BANNER = re.compile(r"^={3,}")
_BLOCK = re.compile(r"^={3,}\s+(?:ERRORS|FAILURES)\s+={3,}$")
_ERROR_AT = re.compile(r"^ERROR at (?P<phase>setup|teardown) of (?P<name>.+)$")
_FIXTURE_MISSING = re.compile(r"fixture '(?P<f>[\w.-]+)' not found")
_FIXTURE_DEF = re.compile(r"^\s*(?:async\s+)?def\s+(?P<f>\w+)\s*\(")


def failed_tests(text: str) -> list[dict[str, str]]:
    """``[{"id", "why", "kind"}]`` for every failing test the output names, in order. ``kind``
    is ERROR for pytest's `ERROR` lines (a fixture or teardown error: the test body may never
    have run) and FAILED for the rest. A test that failed AND errored in teardown has a row of
    each kind; a row is never repeated."""
    seen: dict[tuple[str, str], str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if m := _PYTEST_LINE.match(line):
            kind = "ERROR" if line.startswith("ERROR") else "FAILED"
            seen.setdefault((m["id"], kind), (m["why"] or "")[:LINE_CHARS])
        elif m := _UNITTEST_LINE.match(line):
            where = m["where"]
            tid = where if where.endswith(m["name"]) else f"{where}.{m['name']}"
            seen.setdefault((tid, "FAILED"), "")
    return [{"id": k, "why": v, "kind": kind} for (k, kind), v in seen.items()]


def failure_tails(text: str) -> list[dict[str, str]]:
    """``[{"test", "tail"}]``: the last lines of each failure section, capped."""
    tails: list[dict[str, str]] = []
    name: str | None = None
    body: list[str] = []

    def close() -> None:
        if name is not None and len(tails) < MAX_TAILS:
            lines = [ln[:LINE_CHARS] for ln in body if ln.strip()][-TAIL_LINES:]
            tails.append({"test": name, "tail": "\n".join(lines)[-TAIL_CHARS:]})

    in_block = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if m := _SECTION.match(line.strip()):
            close()
            name, body = m["name"], []
        elif in_block and line == raw.strip() and (m := _SHORT_SECTION.match(line)):
            close()
            name, body = m["name"], []
        elif _BANNER.match(line.strip()):
            close()
            name, body = None, []
            in_block = bool(_BLOCK.match(line.strip()))
        elif name is not None:
            body.append(line)
    close()
    return tails


def failure_evidence(text: str) -> dict[str, Any]:
    """The evidence a gate keeps about the tests that failed in ``text`` ({} when none)."""
    found = failed_tests(text)
    if not found:
        return {}
    tails = failure_tails(text)
    ids = list(dict.fromkeys(f["id"] for f in found))
    kept = [f for f in found if f["id"] in set(ids[:MAX_FAILED_TESTS])]
    ev: dict[str, Any] = {"failed_tests": ids[:MAX_FAILED_TESTS]}
    if len(ids) > MAX_FAILED_TESTS:
        ev["failed_tests_more"] = len(ids) - MAX_FAILED_TESTS
    reasons: dict[str, str] = {}
    errors = _error_fields(kept, tails, reasons)
    if reasons:
        ev["failed_reasons"] = reasons
    if tails:
        ev["failure_tails"] = tails
    return {**ev, **errors}


def _error_fields(
    kept: list[dict[str, str]], tails: list[dict[str, str]], reasons: dict[str, str]
) -> dict[str, Any]:
    """The `errored_*` evidence for the ERROR rows of ``kept``; fills ``reasons`` (failed_reasons)
    for every row: a FAILED row's own reason, else the section's last `E` line. A test that
    failed and also errored keeps its FAILED reason there."""
    errored: dict[str, str] = {}
    where: dict[str, dict[str, str]] = {}
    for f in kept:
        is_error = f["kind"] == "ERROR"
        section = _section_of(f["id"], tails, error=is_error)
        why = f["why"] or (_last_error_line(section[1]) if section else "")
        if is_error:
            errored[f["id"]] = why
            if section:
                where[f["id"]] = _error_place(*section)
        elif why:
            reasons[f["id"]] = why
    for tid, why in errored.items():
        if why:
            reasons.setdefault(tid, why)
    if not errored:
        return {}
    out: dict[str, Any] = {
        "errored_tests": list(errored),
        "errored_reasons": {k: v for k, v in errored.items() if v},
    }
    if both := [t for t in errored if any(f["id"] == t and f["kind"] == "FAILED" for f in kept)]:
        out["failed_also"] = both  # failed in the call AND errored in teardown: listed twice
    if where:
        out["errored_where"] = where
    return out


def _error_place(header: str, tail: str) -> dict[str, str]:
    """Where an error happened: its phase (from `ERROR at setup of x`) and, when the
    traceback shows it, the fixture."""
    at = _ERROR_AT.match(header)
    info = {"phase": at["phase"] if at else "setup"}
    if fixture := _fixture_of(tail):
        info["fixture"] = fixture
    return info


def _section_of(
    test: str, tails: list[dict[str, str]], *, error: bool = False
) -> tuple[str, str] | None:
    """(header, tail) of the failure section that names pytest id ``test``, else None. An
    error's header reads `ERROR at setup of <name>`; a test that failed and then errored in
    teardown has both, so ``error`` picks the one that is meant."""
    for t in tails:
        at = _ERROR_AT.match(t["test"])
        if bool(at) == error and _names(at["name"] if at else t["test"], test):
            return t["test"], t["tail"]
    return None


def _last_error_line(tail: str) -> str:
    """The last `E   message` line of a failure section, without its marker."""
    lines = [ln[1:].strip() for ln in tail.splitlines() if ln.startswith("E ")]
    return lines[-1][:LINE_CHARS] if lines else ""


def _fixture_of(tail: str) -> str:
    """The fixture a setup error came from: named by pytest ("fixture 'x' not found"), or
    the function under an `@pytest.fixture` decorator in the traceback; "" when unknown."""
    if m := _FIXTURE_MISSING.search(tail):
        return m["f"]
    decorated = False
    for ln in tail.splitlines():
        if re.match(r"^\s*@(?:pytest\.)?fixture\b", ln):
            decorated = True
        elif decorated and (
            m := _FIXTURE_DEF.match(ln.replace(">", " ", 1) if ln.startswith(">") else ln)
        ):
            return m["f"]
    return ""


def _error_line(i: str, where: dict[str, Any], why: dict[str, str]) -> str:
    """`  ERROR <id> [setup of fixture x] - message` for one errored test."""
    w = where.get(i) or {}
    place = ""
    if w:
        place = (
            f" [{w['phase']}" + (f" of fixture {w['fixture']}" if w.get("fixture") else "") + "]"
        )
    return f"  ERROR {i}{place} - {why.get(i) or 'no message in the output'}"


def _tail_lines(ev: dict[str, Any]) -> list[str]:
    """The failure sections, then the rerun note, of a gate's evidence."""
    lines = []
    for t in ev.get("failure_tails") or []:
        lines += [f"--- {t['test']} ---", t["tail"]]
    if rr := ev.get("rerun"):
        note = f" ({rr['note']})" if rr.get("note") else ""
        lines.append(f"rerun of the failed tests once: {rr.get('outcome', '?')}{note}")
    return lines


def _groups(ev: dict[str, Any], ids: list[str]) -> tuple[list[str], list[str]]:
    """(the ids that FAILED, the ids that ERRORED); one that did both is in both."""
    erred = [i for i in ids if i in set(ev.get("errored_tests") or [])]
    failed = [i for i in ids if i not in erred or i in (ev.get("failed_also") or [])]
    return failed, erred


def failure_text(ev: dict[str, Any]) -> str:
    """The failing tests of a gate's evidence, as lines for a person ("" when none).
    Failures and errors (a fixture that raised, a teardown that did) are listed apart: an
    error says WHERE (setup or teardown, which fixture) and what it said."""
    ids = ev.get("failed_tests") or []
    if not ids:
        return ""
    why = ev.get("failed_reasons") or {}
    failed, erred = _groups(ev, ids)
    more = int(ev.get("failed_tests_more") or 0)
    lines = []
    if failed or not erred:
        lines.append(f"{len(failed) + (0 if erred else more)} failing test(s):")
        lines += [f"  FAILED {i}" + (f" - {why[i]}" if i in why else "") for i in failed]
    if erred:
        lines.append(f"{len(erred)} error(s) (the test did not get to pass or fail):")
        lines += [
            _error_line(i, ev.get("errored_where") or {}, ev.get("errored_reasons") or {})
            for i in erred
        ]
    if more:
        lines.append(f"  ... and {more} more" + (" (failures or errors)" if erred else ""))
    return "\n".join(lines + _tail_lines(ev))


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
