"""Writing the project's TOML config, safely — the one place that edits it.

Split out of `cli.py` because it is machinery, not a command: `cmd_config` uses it, and
so do `workflow pipeline`, `workflow gate` and `workflow drop`, which are edits to the
same file wearing a friendlier name. Four call sites reaching into one 4,300-line module
for a private helper is how that module got to 4,300 lines.

The order inside `_write_config` is the whole point — compose every edit, validate the
RESULT, then replace the file atomically under a lock. A writer that validates the state
it is replacing has checked nothing, and a truncating write interrupted halfway leaves an
empty config that loads as "no overrides at all" without saying so.
"""

from __future__ import annotations

import sys

from ...services.configwrite import (  # noqa: F401  -- re-exported for cli.py
    _guarded_human_gates,
    _outside_quotes,
    _toml_literal,
    _toml_upsert,
    _value_span,
    _workflow_problems,
    _write_config,
)
from ..context import FAIL, OK, Ctx


def _config_set(a, c: Ctx) -> int:
    """`ddflow config --set <section>.<key> <value>` — edit one key in place.

    Exists because appending is not always possible: TOML forbids a duplicate table, so
    once a section is present the documented "append a block" path fails. Editing in
    place also preserves the surrounding comments, which for this file carry most of
    the reasoning.
    """
    err, _text = _write_config(c.repo, [(a.set, a.value)])
    if err:
        print(err, file=sys.stderr)
        return FAIL
    c.out(
        f"{a.set} = {_toml_literal(a.value)}",
        {"key": a.set, "value": a.value, "path": str(c.repo / ".ddflow" / "config.toml")},
    )
    return OK
