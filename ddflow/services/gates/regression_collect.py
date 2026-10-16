"""Which tests a tree provides, learned from pytest itself (bug B9f3ae012b9).

The restored-test-files check needs to know whether the named test exists at the base. Scanning
the file's text for `def name` misses a test that a module re-exports or generates by import;
`pytest --collect-only` asks the runner that would run it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .defs import GateDef
from .runner import run_command_gate


def tree_provides(command_for: Any, configured: str, root: Path, tests: list[str]) -> bool:
    """True when ``pytest --collect-only`` in ``root`` finds every named test id.

    pytest exits 0 only when each id resolves to at least one test; an id it cannot find
    (a missing file, a name nothing defines, a comment that merely mentions it) is exit 4.
    A runner that cannot be built or run provides nothing: False, never a guess."""
    for spec in tests:
        if not (root / spec.partition("::")[0]).is_file():
            return False
    command = command_for(configured, tests, root)
    if not command:
        return False
    probe = GateDef(
        id="regression_collect",
        title="collect the named tests",
        command=f"{command} --collect-only -q",
    )
    outcome, _ev = run_command_gate(probe, root)
    return outcome == "passed"
