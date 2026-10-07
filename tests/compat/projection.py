"""What a ddflow release makes of a project's event log, as plain JSON (B-uni-compat-tests).

ONE file, run by two interpreters: `scripts/ci/compat-matrix` runs it under each OLD release's
own wheel when it builds that release's fixture (writing `expected.json`, the old release's
reading of the log it wrote), and tests/compat/test_new_reads_old.py imports it under the
CURRENT code to read the same log. So it may only use what every release since 0.1.3 has:
`ddflow.core.model.fold`, `ddflow.infra.log.EventLog(root).read_all()` and the record
dataclasses, read field by field.

A record is dumped with every dataclass field the running release defines. The comparison
(`differences`) walks the EXPECTED side's fields only: a field a newer release added is not a
difference, a field the old release had whose value the new release reads differently is.
RECORDS are compared both ways: a record the new release folds out of the same log that the
old one did not is a difference too.

    python tests/compat/projection.py <project-root>      # prints the projection
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import sys
from pathlib import Path
from typing import Any

#: The State collections compared: every record kind the log carries that 0.1.3 already folded.
COLLECTIONS = ("items", "bugs", "lessons", "research", "decisions", "memories", "sessions", "jobs")


def plain(value: Any) -> Any:
    """JSON-ready, deterministic: dataclasses field by field, sets sorted, tuples as lists."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(plain(v) for v in value)
    return value


def _read(root: Path) -> list[Any]:
    from ddflow.infra.log import EventLog

    # A read must leave nothing behind in the fixture; releases before `cache_writes` existed
    # take only the root.
    kwargs = {}
    if "cache_writes" in inspect.signature(EventLog).parameters:
        kwargs["cache_writes"] = False
    return EventLog(root, **kwargs).read_all()


def project(root: Path | str) -> dict[str, Any]:
    """The folded state of the project at ``root``, strictly: an event kind the running
    release cannot interpret raises instead of being skipped."""
    import ddflow
    from ddflow.core.model import fold

    state = fold(_read(Path(root)), strict=True)
    out: dict[str, Any] = {"version": ddflow.__version__, "event_count": state.event_count}
    for name in COLLECTIONS:
        records = getattr(state, name)
        out[name] = {key: plain(records[key]) for key in sorted(records)}
    return out


def differences(expected: Any, actual: Any, path: str = "") -> list[str]:
    """Every place ``actual`` disagrees with ``expected``: expected's fields only, but every
    record of a collection either side has. Values compare by type as well (1 is not True)."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        out: list[str] = []
        if path in COLLECTIONS:
            out.extend(f"{path}.{key}: unexpected record" for key in actual if key not in expected)
        for key, want in expected.items():
            where = f"{path}.{key}" if path else key
            if key not in actual:
                out.append(f"{where}: missing (expected {want!r})")
            else:
                out.extend(differences(want, actual[key], where))
        return out
    if isinstance(expected, list) and isinstance(actual, list) and len(expected) == len(actual):
        out = []
        for i, (want, got) in enumerate(zip(expected, actual, strict=True)):
            out.extend(differences(want, got, f"{path}[{i}]"))
        return out
    if type(expected) is type(actual) and expected == actual:
        return []
    return [f"{path}: expected {expected!r}, got {actual!r}"]


if __name__ == "__main__":
    print(json.dumps(project(sys.argv[1]), indent=1, sort_keys=True))
