"""The registry of document kinds, and how a kind turns a Query into a document.

See the package docstring (``ddflow.services.export``) for the plug-in contract.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from ..prompts import Template, TemplateError
from ..prompts import render as render_template
from .frame import DEFAULT_MAX_BYTES, frame, normalize, truncate
from .query import EXIT_REFUSED, ExportError, Query

#: How a kind is written to its target (the writer, B-export-write, implements these).
WHOLE = "whole"  # the file is entirely generated
REGION = "region"  # only a marked region of a hand-written file
APPEND = "append"  # entries appended after the last exported event id
UPDATE_MODES = (WHOLE, REGION, APPEND)


@dataclass(frozen=True)
class Filters:
    """The one filter vocabulary. A kind lists in ``DocKind.filters`` the names it honours;
    giving it any other filter is refused (exit 3) rather than silently ignored."""

    since: str = ""  # ISO date/timestamp prefix; entries at or after it
    limit: int = 0  # keep at most N entries (0 = no limit)
    status: str = ""  # a kind-defined status word (open, fixed, ...)
    phase: str = ""  # one phase id
    session: str = ""  # one session id

    def given(self) -> set[str]:
        """Names set to something other than their default."""
        return {f.name for f in fields(self) if getattr(self, f.name) != f.default}


@dataclass(frozen=True)
class DocKind:
    name: str  # also the template stem: templates/export/<name>.md.j2
    default_target: str  # repo-relative path the writer uses unless told otherwise
    data: Callable[[Query, Filters], Mapping[str, Any]]  # pure: no I/O, no clock
    update_mode: str = WHOLE
    filters: frozenset[str] = frozenset()  # Filters field names this kind honours
    title: str = ""  # one line for `export list`

    def __post_init__(self) -> None:
        if self.update_mode not in UPDATE_MODES:
            raise ValueError(f"{self.name}: update_mode {self.update_mode!r} not in {UPDATE_MODES}")
        bad = set(self.filters) - {f.name for f in fields(Filters)}
        if bad:
            raise ValueError(f"{self.name}: unknown filter names {sorted(bad)}")


_KINDS: dict[str, DocKind] = {}


def register(kind: DocKind) -> DocKind:
    """Add a kind. Re-registering the same name is an error (two modules claiming one)."""
    if kind.name in _KINDS:
        raise ValueError(f"export kind {kind.name!r} is already registered")
    _KINDS[kind.name] = kind
    return kind


def unregister(name: str) -> None:
    """Remove a kind (tests)."""
    _KINDS.pop(name, None)


_discovered = [False]


def discover() -> None:
    """Import every ``ddflow/services/export/kind_*.py`` once, so each registers itself.

    Convention over a shared list: a new kind is one new file, and no two kinds edit the
    same line.
    """
    if _discovered[0]:
        return
    _discovered[0] = True
    pkg = importlib.import_module(__package__)
    for m in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
        if m.name.startswith("kind_"):
            importlib.import_module(f"{__package__}.{m.name}")


def names() -> list[str]:
    discover()
    return sorted(_KINDS)


def get(name: str) -> DocKind:
    discover()
    try:
        return _KINDS[name]
    except KeyError:
        raise ExportError(
            f"unknown document kind {name!r}. Known: {', '.join(sorted(_KINDS)) or '(none)'}",
            EXIT_REFUSED,
        ) from None


def resolve_template(
    kind: str,
    repo: Path | None = None,
    overrides: Mapping[str, str] | None = None,
    builtin: Path | None = None,
) -> Template:
    """The Jinja2 template for ``kind``; first hit wins, like ``services.prompts``:

    1. ``[export.<kind>].template`` -- ``overrides[kind]``, an explicit path (relative to
       the repo); a configured path that does not exist is an error, never a fallback;
    2. ``<repo>/.ddflow/templates/export/<kind>.md.j2``;
    3. the shipped ``ddflow/templates/export/<kind>.md.j2``.
    """
    explicit = (overrides or {}).get(kind, "")
    if explicit:
        path = Path(explicit)
        if not path.is_absolute() and repo:
            path = Path(repo) / path
        if not path.is_file():
            raise ExportError(f"[export.{kind}].template points at {path}, which does not exist")
        return Template(kind, path.read_text("utf-8"), "config", path)
    if repo:
        path = Path(repo) / ".ddflow" / "templates" / "export" / f"{kind}.md.j2"
        if path.is_file():
            return Template(kind, path.read_text("utf-8"), "project", path)
    if builtin is None:
        from ...infra.paths import templates_dir

        builtin = templates_dir() / "export"
    path = builtin / f"{kind}.md.j2"
    if not path.is_file():
        raise ExportError(f"shipped template {kind}.md.j2 is missing from the package")
    return Template(kind, path.read_text("utf-8"), "builtin", path)


def render_body(
    kind: DocKind | str,
    query: Query,
    filters: Filters | None = None,
    *,
    repo: Path | None = None,
    overrides: Mapping[str, str] | None = None,
    builtin: Path | None = None,
) -> str:
    """The document body only: ``kind.data(query, filters)`` through the kind's template.

    Raises ``ExportError`` (refused) for a filter the kind does not honour, and (could not
    run) for a template that fails to render -- a StrictUndefined miss is a bug in the
    kind or the template, and must not become an empty document.
    """
    k = get(kind) if isinstance(kind, str) else kind
    f = filters or Filters()
    extra = f.given() - set(k.filters)
    if extra:
        raise ExportError(
            f"document kind {k.name!r} does not take: {', '.join(sorted(extra))}"
            f" (takes: {', '.join(sorted(k.filters)) or 'no filters'})",
            EXIT_REFUSED,
        )
    tmpl = resolve_template(k.name, repo, overrides, builtin)
    try:
        return normalize(render_template(tmpl, **dict(k.data(query, f))))
    except TemplateError as exc:
        raise ExportError(str(exc)) from exc


def render_document(
    kind: DocKind | str,
    query: Query,
    filters: Filters | None = None,
    *,
    repo: Path | None = None,
    overrides: Mapping[str, str] | None = None,
    builtin: Path | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    extra: dict[str, str] | None = None,
    version: str = "",
) -> str:
    """The full framed document (header + body), capped to ``max_bytes`` (0 = uncapped).

    Truncation happens BEFORE framing, so the header digest always matches the bytes
    shown and ``frame.hand_edited`` is False for any output. Pure: same inputs, same bytes.
    """
    k = get(kind) if isinstance(kind, str) else kind
    body = render_body(k, query, filters, repo=repo, overrides=overrides, builtin=builtin)
    if not version:
        import ddflow

        version = str(ddflow.__version__)
    return frame(truncate(body, max_bytes), k.name, version, extra)
