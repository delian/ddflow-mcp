"""Which tests a tree provides, learned from pytest itself (bug B9f3ae012b9).

The restored-test-files check needs to know whether the named test exists at the base. Scanning
the file's text for `def name` misses a test that a module re-exports or generates by import;
`pytest --collect-only` asks the runner that would run it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .defs import GateDef
from .runner import run_command_gate


def _scan(root: Path, tests: list[str]) -> bool:
    """The fallback when pytest cannot answer (a collection error, no runner): the file
    defines each name, as text. A definition, not a substring."""
    for spec in tests:
        rel, _, rest = spec.partition("::")
        src = root / rel
        if not src.is_file():
            return False
        name = rest.split("::")[-1].split("[")[0]
        if name and not re.search(
            rf"^\s*(?:async\s+)?(?:def|class)\s+{re.escape(name)}\b",
            src.read_text("utf-8", errors="replace"),
            re.M,
        ):
            return False
    return True


def tree_provides(
    command_for: Any, configured: str, root: Path, tests: list[str], *, scan: bool = True
) -> bool:
    """Does ``root`` provide every named test?

    ``pytest --collect-only`` exit 0 says so: each id resolved to a test, a re-export
    included. Anything else is pytest not confirming it -- the id is absent, OR the file
    cannot be collected at all (an import the base cannot satisfy: pytest then reports the
    id "not found" too), OR it timed out or has no runner -- and the text scan decides. The
    scan only keeps the test in the restored check; a base file pytest cannot import still
    ends there as "not run" (exit 4), exactly as before this probe existed. ``scan=False``:
    only pytest's own exit 0 counts (the static name check, which has its own strict scan)."""
    if any(not (root / s.partition("::")[0]).is_file() for s in tests):
        return False
    command = command_for(configured, tests, root)
    if command:
        probe = GateDef(
            id="regression_collect",
            title="collect the named tests",
            command=f"{command} --collect-only -q",
        )
        if run_command_gate(probe, root)[0] == "passed":
            return True
    return scan and _scan(root, tests)
