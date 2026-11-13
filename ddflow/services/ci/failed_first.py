"""Run the last failed tests first, and record how soon a run showed its first failure.

A rerun after a fix used to execute the whole selected set in default order, so a test that was
still broken reported at the end of a run that costs minutes (FF-failed-first). ddflow orders
from its OWN gate evidence (the item's last failed `ci` and `unit_tests` ids, and the tests of
the files its diff touches), not from pytest's cache: its runs happen in scratch worktrees with
a per-run cache directory and under xdist.

How it reaches the project's pytest: `session` writes the ids file and the plugin
(`ddflow/templates/pytest/ddflow_failed_first.py.txt`, standard library only, because it runs in
whatever interpreter the project's tests use) into a scratch directory, and returns the
environment that finds it (the directory, with a pytest11 entry point, on `PYTHONPATH`). The plugin orders in ``pytest_collection_modifyitems`` before xdist distributes,
identically in every worker, and appends its timestamps to a file that `read_outcome` reads. A
missing or unreadable ids file is the ordinary order, never an error.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...adapters.env import DEFAULT as ENV
from ...infra import fsio

#: The file naming the ids to run first (JSON: ``{"ids": [...], "files": [...]}``).
IDS_ENV = "DDFLOW_FAILED_FIRST_FILE"
#: The JSON-lines file the plugin appends its timestamps to.
OUT_ENV = "DDFLOW_FAILED_FIRST_OUT"
#: Set by the plugin to its pid: a pytest a test starts inside the run stands down.
ACTIVE_ENV = "DDFLOW_FAILED_FIRST_ACTIVE"
#: The module name of the plugin copy, loaded as a pytest11 entry point.
PLUGIN = "ddflow_failed_first"
PLUGIN_SOURCE = (
    Path(__file__).resolve().parent.parent.parent / "templates" / "pytest" / f"{PLUGIN}.py.txt"
)

#: Reads the whole failing list a gate's ``failing_ids`` evidence stub names (`gates.baseline`).
BlobReader = Callable[[Path, Any], list[str]]


@dataclass(frozen=True)
class Plan:
    """What to run first: failed test ids, then the test files the diff touches."""

    ids: tuple[str, ...] = ()
    files: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.ids or self.files)


@dataclass
class Session:
    """The environment a run needs (empty without a plan) and where it will find the outcome."""

    env: dict[str, str] = field(default_factory=dict)
    out: Path | None = None

    def outcome(self) -> dict[str, Any]:
        """What the plugin noted (see `read_outcome`); {} when there was no plan."""
        if not self.env or self.out is None:
            return {}
        return read_outcome(self.out)


@contextmanager
def session(plan: Plan, parent_env: Mapping[str, str] | None = None) -> Iterator[Session]:
    """Write the ids file and the plugin into a scratch directory for one run, yield the
    environment that finds them (``parent_env``'s own PYTHONPATH kept), and
    remove the directory after. No plan: an empty session, nothing written."""
    if not plan:
        yield Session()
        return
    base = dict(ENV.snapshot() if parent_env is None else parent_env)
    with fsio.scratch_dir("ddflow-failed-first-", ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        fsio.atomic_write(root / f"{PLUGIN}.py", PLUGIN_SOURCE.read_text(encoding="utf-8"))
        ids_file = root / "ids.json"
        fsio.atomic_write(ids_file, json.dumps({"ids": list(plan.ids), "files": list(plan.files)}))
        out = root / "out.jsonl"
        # Loaded as a pytest11 entry point of a dist-info beside the module, not by `-p`: a pytest
        # whose environment lost PYTHONPATH (a test that builds its own) then simply does not
        # find the plugin, where `-p` would stop it with "Error importing plugin".
        info = root / f"{PLUGIN}-0.dist-info"
        info.mkdir()
        fsio.atomic_write(info / "METADATA", f"Metadata-Version: 2.1\nName: {PLUGIN}\nVersion: 0\n")
        fsio.atomic_write(info / "entry_points.txt", f"[pytest11]\n{PLUGIN} = {PLUGIN}\n")
        old_path = base.get("PYTHONPATH", "")
        env = {
            IDS_ENV: str(ids_file),
            OUT_ENV: str(out),
            ACTIVE_ENV: "",  # a run of its own: not nested in the one that started this process
            "PYTHONPATH": f"{root}{os.pathsep}{old_path}" if old_path else str(root),
        }
        yield Session(env=env, out=out)


def read_outcome(out: Path) -> dict[str, Any]:
    """What the plugin noted. ``ran_failed_first`` is the ids it REALLY ordered first (those the
    collection held), never the plan: absent when no pytest session ordered anything, so a run
    the plugin never reached cannot claim it. ``first_failure_at_s`` is the seconds from the start
    of the run's first pytest session (collection included) to the earliest failed test after
    it, ``first_failure_id`` which test; absent without a failure. By timestamp, not line order."""
    got: dict[str, Any] = {}
    starts: list[float] = []
    fails: list[tuple[float, str]] = []
    ordered: list[str] = []
    try:
        lines = Path(out).read_text(encoding="utf-8").splitlines()
    except OSError:
        return got
    for line in lines:
        try:
            row = json.loads(line)
            ev = row["ev"]
            if ev == "ordered":
                ordered += [str(i) for i in row["ids"]]
            else:
                at = float(row["at"])
                if ev == "start":
                    starts.append(at)
                elif ev == "fail":
                    fails.append((at, str(row.get("id", ""))))
        except (ValueError, KeyError, TypeError):
            continue
    if ordered:
        got["ran_failed_first"] = list(dict.fromkeys(ordered))
    after = sorted(f for f in fails if starts and f[0] >= min(starts))
    if after:
        got["first_failure_at_s"] = round(after[0][0] - min(starts), 3)
        got["first_failure_id"] = after[0][1]
    return got


def previous_failing_ids(
    repo: Path, item: Any, max_ids: int, read_full: BlobReader | None = None
) -> list[str]:
    """The ids an item's last FAILED ``ci`` and ``unit_tests`` runs reported, ``ci`` first, no
    id twice, at most ``max_ids`` (0: none). The whole list comes from the blob the run kept
    aside when the evidence cut it to a sample (``read_full``, `gates.baseline.read_full`); a
    gate that last passed, or never ran, gives nothing."""
    if max_ids <= 0 or item is None:
        return []
    found: list[str] = []
    for gate in ("ci", "unit_tests"):
        rec = (getattr(item, "gates", None) or {}).get(gate)
        if rec is None or getattr(rec, "outcome", "") != "failed":
            continue
        ev = rec.evidence or {}
        kept = read_full(repo, ev.get("failing_ids")) if read_full else []
        found += kept or [str(i) for i in ev.get("failed_tests") or () if isinstance(i, str)]
    return list(dict.fromkeys(found))[:max_ids]
