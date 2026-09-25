"""Where the package and its shipped data live — asked once, answered once.

Four modules each worked this out for themselves from their own `__file__`:

    services/prompts.py     Path(__file__).resolve().parent / "templates" / "prompts"
    services/companions.py  Path(__file__).resolve().parent / "templates" / ...
    services/adopt.py       Path(__file__).resolve().parents[1]
    services/enforce.py     Path(__file__).resolve().parents[1]

Every one of them was correct while those files sat directly under `ddflow/`, and every
one of them broke the moment the package was split into layers — silently, because a
path that does not exist produces a "templates are missing, reinstall the package"
message that blames packaging, and a wrong `PYTHONPATH` produces a spawned server that
imports nothing, prints nothing, and leaves its client waiting on a handshake forever.
The full suite ran for two hours before that one was killed.

Counting directory levels above `__file__` encodes a file's *location* into its
*behaviour*. Anchoring on the package object instead does not: `ddflow.__file__` is
wherever the package is, installed or in a source tree, and a module that moves between
layers keeps working.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def package_dir() -> Path:
    """The `ddflow/` package directory itself."""
    import ddflow

    return Path(ddflow.__file__).resolve().parent


@lru_cache(maxsize=1)
def package_parent() -> Path:
    """The directory that must be on `PYTHONPATH` to `import ddflow`.

    Written into the MCP configs `adopt` generates and into the git hook `enforce`
    installs, so both spawn a Python that can actually find the package.
    """
    return package_dir().parent


def templates_dir() -> Path:
    """Shipped template data. Present in a wheel; `tests/test_packaging.py` proves it.

    The invariant that test exists for: templates living *beside* the package rather
    than *inside* it were absent from every wheel while being perfectly present in
    every source checkout — invisible to developers and fatal to users.
    """
    return package_dir() / "templates"
