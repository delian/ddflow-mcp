"""Where a document section's content comes from, without Python.

A section's ``source`` is ``<kind>`` or ``<kind>:<argument>``: ``kind:<export kind>[.<key>]``,
``query:<record query>``, ``fragments:<dir>``, ``file:<path>[#anchor]``, ``cmd:<name>``,
``registry:<name>``, and the placeholders ``ask``, ``draft``, ``static`` (a person or an agent
writes those; the engine yields nothing). Each kind is one file ``source_<kind>.py`` that
registers a `SourceKind`, so a new one edits no list. A source yields plain data, rendered
through the sandboxed Jinja environment of the export engine with the section's template
(inline, or ``file:<path>`` in the repo) or the source's default.

`check` validates a source when a type loads. A source that cannot be read raises
`SourceUnavailable`, never an empty section that looks like a pass.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...core.registry import Registry
from ..export import registry as export_registry
from ..export.query import EXIT_REFUSED, EXIT_UNAVAILABLE, ExportError, Query
from .spec import DocType, Section

SEPARATOR = ":"


class SourceError(ExportError):
    """A source that is not one, or one that could not be read."""

    def __init__(self, message: str, code: int = EXIT_REFUSED) -> None:
        super().__init__(message, code)


class SourceUnavailable(SourceError):
    """The source could not run (a missing binary, an unreadable file): exit 2, not a pass."""

    def __init__(self, message: str) -> None:
        super().__init__(message, EXIT_UNAVAILABLE)


@dataclass
class Context:
    """What a source may read: the loaded log, the repo, and the operator's configuration."""

    query: Query | None = None
    repo: Path | None = None
    commands: Mapping[str, str] = field(default_factory=dict)  # cmd: name -> declared line
    command_timeout_s: float = 20.0
    max_bytes: int = 64_000
    filters: export_registry.Filters = field(default_factory=export_registry.Filters)
    overrides: Mapping[str, str] = field(default_factory=dict)  # [export.<kind>].template
    redactor: Callable[[str], str] | None = None


@dataclass(frozen=True)
class SourceKind:
    """One source kind: ``check(arg)`` returns the checked argument (or raises SourceError),
    ``data(arg, ctx)`` the plain data a template renders, ``template`` the default one."""

    name: str
    check: Callable[[str], str] = lambda arg: arg
    data: Callable[[str, Context], Mapping[str, Any]] | None = None  # None: a placeholder
    template: str = "{{ text }}"
    #: Resolves the template at render time instead (the export kind's own, with overrides).
    template_for: Callable[[str, Context], Any] | None = None
    needs_log: bool = False


SOURCES: Registry[SourceKind] = Registry(
    "document source", key=lambda s: s.name, package=__package__, prefix="source_"
)


def register(kind: SourceKind) -> SourceKind:
    return SOURCES.register(kind)


@dataclass(frozen=True)
class Source:
    kind: SourceKind
    arg: str

    @property
    def text(self) -> str:
        return f"{self.kind.name}{SEPARATOR}{self.arg}" if self.arg else self.kind.name


def parse(text: str) -> Source:
    """The checked `Source` of a section's ``source`` text."""
    name, _, arg = text.strip().partition(SEPARATOR)
    kind = SOURCES.get(name)
    if kind is None:
        raise SourceError(
            f"unknown document source {name!r} in {text!r}. Known: {', '.join(SOURCES)}"
        )
    if arg and kind.data is None:
        raise SourceError(f"source {name!r} takes no argument (got {text!r})")
    return Source(kind, kind.check(arg))


def check(text: str) -> None:
    """Validate ``text`` as a source; the spec loader calls this for every section."""
    if text.strip():
        parse(text)


def is_generated(section: Section) -> bool:
    """True when the engine can fill ``section``: a source that yields data."""
    return bool(section.source.strip()) and parse(section.source).kind.data is not None


def resolve(src: Source, ctx: Context) -> Mapping[str, Any]:
    if src.kind.needs_log and ctx.query is None:
        raise SourceUnavailable(f"source {src.text!r} needs the event log, and none was loaded")
    assert src.kind.data is not None
    return src.kind.data(src.arg, ctx)


def _template(section: Section, src: Source, ctx: Context) -> Any:
    own = section.template
    if own.startswith("file:"):
        path = (ctx.repo or Path()) / own[len("file:") :]
        if ctx.repo is None or not path.resolve().is_relative_to(ctx.repo.resolve()):
            raise SourceError(f"section {section.id!r}: template {own!r} is not inside the repo")
        if not path.is_file():
            raise SourceError(f"section {section.id!r}: template {own!r} does not exist")
        return path.read_text("utf-8")
    if own:
        return own
    if src.kind.template_for is not None:
        return src.kind.template_for(src.arg, ctx)
    return src.kind.template


def render_section(section: Section, ctx: Context) -> str:
    """One section's body (no heading), or "" for a section the engine does not fill."""
    if not section.source.strip():
        return ""
    src = parse(section.source)
    if src.kind.data is None:
        return ""
    data = dict(resolve(src, ctx))
    version = int(data.pop("schema_version", 1))
    return export_registry.render(
        _template(section, src, ctx), data, schema_version=version, doc=section.id
    )


def render_type(dt: DocType, ctx: Context) -> str:
    """The generated body of a document type: each filled section, headed unless its heading
    is empty (a type whose one section is the whole document), joined by blank lines."""
    parts = []
    for sec in dt.sections:
        if not is_generated(sec):
            continue
        body = render_section(sec, ctx)
        head = f"{'#' * sec.level} {sec.heading}\n\n" if sec.heading else ""
        parts.append(head + body.strip("\n") if head else body.rstrip("\n"))
    return "\n\n".join(parts) + "\n" if parts else ""


def check_relative(rel: str, what: str) -> str:
    """A repo-relative path with no ``..`` and no absolute or drive part, or `SourceError`."""
    p = Path(rel)
    if not rel or p.is_absolute() or ".." in p.parts or "\\" in rel or "\0" in rel:
        raise SourceError(f"source {what!r}: {rel!r} is not a path inside the repo")
    return rel


def tracked_in(ctx: Context, rel: str) -> list[str]:
    """The tracked files at or below ``rel`` (git's own listing, sorted), `SourceUnavailable`
    when there is no repo or git could not answer: a source never reads an untracked file."""
    from ...infra import git

    if ctx.repo is None:
        raise SourceUnavailable("a file source needs the repository, and none was given")
    root = ctx.repo.resolve()
    if not (root / rel).resolve().is_relative_to(root):  # a symlink out of the repo
        raise SourceError(f"{rel!r} resolves outside the repo")
    found = git.files(root, "tracked", pathspec=(rel,), literal=True)
    if found is None:
        raise SourceUnavailable(f"git could not list the tracked files under {rel!r}")
    return sorted(f for f in found if (root / f).resolve().is_relative_to(root))


def read_capped(ctx: Context, rel: str) -> str:
    """The text of one tracked file, `SourceError` past ``ctx.max_bytes`` (never truncated
    silently), `SourceUnavailable` when it cannot be read."""
    path = (ctx.repo or Path()) / rel
    try:
        if path.stat().st_size > ctx.max_bytes:
            raise SourceError(f"{rel} is {path.stat().st_size} bytes, over the {ctx.max_bytes} cap")
        return path.read_text("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SourceUnavailable(f"could not read {rel}: {exc}") from None
