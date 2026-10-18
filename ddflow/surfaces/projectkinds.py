"""Declare the project's own record kinds (``.ddflow/kinds/*.toml``) when a surface starts.

The CLI context and the MCP server call `declare` as soon as they know the repo, so search,
recall and the record resources see a project kind exactly like a shipped one, whichever
command ran first. A repo with no ``.ddflow/kinds`` costs one directory check and imports
nothing. A file that is not a kind never stops a command: it is reported and skipped (the
`record` command says it again, with the same message).
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path


def declare(repo: Path | str) -> str:
    """Declare the project's kinds; "" when all went well or there are none, else why not."""
    root = Path(repo) / ".ddflow" / "kinds"
    if not root.is_dir():
        return ""
    try:
        import_module("ddflow.api.record_kind_data").load_project(repo)
    except ValueError as exc:  # KindDataError
        return str(exc)
    return ""
