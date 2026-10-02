"""The registry of document kinds, and how a kind turns a Query into a document.

See the package docstring (``ddflow.services.export``) for the plug-in contract.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import pkgutil
import re
import signal
import textwrap
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import jinja2
from jinja2.sandbox import SandboxedEnvironment

from ..prompts import Template
from .frame import DEFAULT_MAX_BYTES, frame, normalize, one_line, truncate
from .query import EXIT_REFUSED, ExportError, Query

#: How a kind is written to its target (the writer, B-export-write, implements these).
WHOLE = "whole"  # the file is entirely generated
REGION = "region"  # only a marked region of a hand-written file
APPEND = "append"  # entries appended after the last exported event id
UPDATE_MODES = (WHOLE, REGION, APPEND)

#: Room a capped document keeps for its body: a truncation footer plus a little text.
MIN_BODY_BYTES = 80


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
    #: Version of the data contract this kind hands its template (the variable names and
    #: shapes). Templates see it as ``schema_version``; ddflow only ever ADDS fields within
    #: one version, so an ejected or user template keeps rendering across upgrades.
    schema_version: int = 1

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
    pkg = importlib.import_module(__package__)
    for m in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
        if m.name.startswith("kind_"):
            importlib.import_module(f"{__package__}.{m.name}")
    _discovered[0] = True  # only after every kind imported: a failure re-raises next call


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


def _read(path: Path) -> str:
    try:
        return path.read_text("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ExportError(f"could not read template {path}: {exc}") from exc


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
        return Template(kind, _read(path), "config", path)
    if repo:
        path = Path(repo) / ".ddflow" / "templates" / "export" / f"{kind}.md.j2"
        if path.is_file():
            return Template(kind, _read(path), "project", path)
    return shipped_template(kind, builtin)


#: Seconds a template may run before it is reported as an error (an accidental or hostile
#: loop in a template that came with a cloned repository must not hang the server).
RENDER_TIMEOUT_S = 10.0


def md_escape(value: object) -> str:
    """Escape Markdown syntax in free text and fold it onto one line."""
    return re.sub(r"([\\`*_{}\[\]<>#|~])", r"\\\1", " ".join(str(value).split()))


def wrap(value: object, width: int = 72) -> str:
    """Re-flow text to ``width`` columns (existing line breaks are folded first)."""
    return textwrap.fill(" ".join(str(value).split()), width=int(width))


def date(value: object) -> str:
    """The ``YYYY-MM-DD`` part of an ISO timestamp (``""`` stays ``""``)."""
    return str(value)[:10]


def truncate_text(value: object, limit: int = 140) -> str:
    """One line, shortened at a word boundary with `` ...`` (frame.one_line)."""
    return one_line(str(value), int(limit))


def bar(done: object, total: object, width: int = 10) -> str:
    """A text progress bar, ``[####......]``; an empty total renders an empty bar."""
    w, t = int(width), int(total)  # type: ignore[call-overload]
    n = 0 if t <= 0 else min(w, round(w * int(done) / t))  # type: ignore[call-overload]
    return "[" + "#" * n + "." * (w - n) + "]"


#: The filters a template may use, besides Jinja's built-in set. Documented contract:
#: adding to it is compatible, renaming or removing is not.
FILTERS: dict[str, Callable[..., str]] = {
    "md_escape": md_escape,
    "wrap": wrap,
    "date": date,
    "truncate": truncate_text,  # overrides Jinja's built-in: one definition, word-boundary
    "bar": bar,
}

_PLAIN = (str, int, float, bool, type(None))


def plain(value: Any, path: str = "data") -> Any:
    """``value`` as JSON-like plain data (dict/list/str/int/float/bool/None), or raise.

    A template receives data, not objects: no model instances, no methods to reach a
    repo, a file or a process through. A kind returning anything else is a bug in the
    kind and is reported as one, naming the path to the offending value.
    """
    if isinstance(value, _PLAIN):
        return value
    if isinstance(value, Mapping):
        out = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise ExportError(f"{path}: key {k!r} is not a string")
            out[k] = plain(v, f"{path}.{k}")
        return out
    if isinstance(value, (list, tuple)):
        return [plain(v, f"{path}[{i}]") for i, v in enumerate(value)]
    raise ExportError(f"{path}: {type(value).__name__} is not plain data (dict/list/str/number)")


def _environment() -> SandboxedEnvironment:
    # bandit B701: Markdown, never HTML; escaping would corrupt the output. The sandbox is
    # the point: a template may come from a cloned repository.
    env = SandboxedEnvironment(  # nosec B701
        undefined=jinja2.StrictUndefined, trim_blocks=True, lstrip_blocks=True, autoescape=False
    )
    env.filters.update(FILTERS)
    return env


@contextlib.contextmanager
def _time_limit(seconds: float):
    """Raise ``TimeoutError`` after ``seconds`` (main thread, where SIGALRM exists).

    Elsewhere (a server worker thread, a platform with no SIGALRM) the sandbox's own
    limits still apply (range() is capped, output is size-capped by the caller) but this
    deadline cannot interrupt a pure-compute loop.
    """
    usable = (
        seconds > 0
        and hasattr(signal, "setitimer")
        and threading.current_thread() is threading.main_thread()
    )
    if not usable:
        yield
        return

    def fire(_sig, _frame):
        raise TimeoutError(f"template ran longer than {seconds:g}s")

    old = signal.signal(signal.SIGALRM, fire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def render(
    template: Template | str,
    data: Mapping[str, Any],
    *,
    schema_version: int = 1,
    timeout_s: float = RENDER_TIMEOUT_S,
) -> str:
    """Render ``template`` (a ``Template`` or its text) over plain ``data``, sandboxed.

    The template sees ``data``'s keys, ``schema_version`` and the ``FILTERS`` -- nothing
    else. Any failure (syntax, undefined variable, sandbox violation, timeout) raises
    ``ExportError`` (exit 2, could not run) naming the template file and line when known;
    nothing is partially returned. Output is normalized to one trailing newline.
    """
    name = template.name if isinstance(template, Template) else "<template>"
    where = str(template.path) if isinstance(template, Template) and template.path else name
    text = template.text if isinstance(template, Template) else template
    ctx = plain(dict(data))
    ctx["schema_version"] = schema_version
    try:
        with _time_limit(timeout_s):
            return normalize(_environment().from_string(text).render(**ctx))
    except jinja2.TemplateSyntaxError as exc:
        raise ExportError(f"{where}:{exc.lineno}: {exc.message}") from exc
    except jinja2.TemplateError as exc:  # undefined variable, SecurityError, ...
        line = _template_line(exc)
        raise ExportError(f"{where}{':' + str(line) if line else ''}: {exc}") from exc
    except (
        TimeoutError,
        RecursionError,
        OverflowError,
        TypeError,
        ValueError,
        ArithmeticError,
    ) as exc:
        raise ExportError(f"{where}: {type(exc).__name__}: {exc}") from exc


def _template_line(exc: BaseException) -> int:
    tb = exc.__traceback__
    line = 0
    while tb is not None:
        if tb.tb_frame.f_code.co_filename in ("<template>",):
            line = tb.tb_lineno
        tb = tb.tb_next
    return line


def template_digest(text: str) -> str:
    """12 hex chars of sha256 over a template's text: what drift detection compares."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def shipped_digest(kind: str, builtin: Path | None = None) -> str:
    """The digest of the SHIPPED template for ``kind`` (never an override).

    An eject records it next to the copy; ``validate``/doctor compare it with this to say
    "the shipped default changed since you ejected".
    """
    return template_digest(shipped_template(kind, builtin).text)


def shipped_template(kind: str, builtin: Path | None = None) -> Template:
    """The shipped default for ``kind``, ignoring any override (what eject copies)."""
    if builtin is None:
        from ...infra.paths import templates_dir

        builtin = templates_dir() / "export"
    path = builtin / f"{kind}.md.j2"
    if not path.is_file():
        raise ExportError(f"shipped template {kind}.md.j2 is missing from the package")
    return Template(kind, _read(path), "builtin", path)


def render_body(
    kind: DocKind | str,
    query: Query,
    filters: Filters | None = None,
    *,
    repo: Path | None = None,
    overrides: Mapping[str, str] | None = None,
    builtin: Path | None = None,
    template: Template | str | None = None,
    timeout_s: float = RENDER_TIMEOUT_S,
) -> str:
    """The document body only: ``kind.data(query, filters)`` through the kind's template.

    ``template`` renders once with an ad hoc template instead of the resolved one
    (``export --template``). Raises ``ExportError`` (refused, exit 3) for a filter the
    kind does not honour, and (could not run, exit 2) for a data/template failure -- a
    StrictUndefined miss or a sandbox violation must not become an empty document.
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
    tmpl = template if template is not None else resolve_template(k.name, repo, overrides, builtin)
    return render(tmpl, k.data(query, f), schema_version=k.schema_version, timeout_s=timeout_s)


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
    template: Template | str | None = None,
) -> str:
    """The full framed document (header + body), capped to ``max_bytes`` (0 = uncapped).

    ``max_bytes`` bounds the WHOLE document: the header's length is reserved before the
    body is truncated; a cap too small for header + footer is refused (exit 3) unless the
    whole document already fits in it. Truncation happens BEFORE framing, so the header digest always
    matches the bytes shown and ``frame.hand_edited`` is False for any output. Pure: same
    inputs, same bytes.
    """
    k = get(kind) if isinstance(kind, str) else kind
    body = render_body(
        k, query, filters, repo=repo, overrides=overrides, builtin=builtin, template=template
    )
    if not version:
        import ddflow

        version = str(ddflow.__version__)
    if max_bytes > 0:
        overhead = len(frame("", k.name, version, extra).encode("utf-8"))
        fits = overhead + len(normalize(body).encode("utf-8")) <= max_bytes
        if not fits and max_bytes < overhead + MIN_BODY_BYTES:
            raise ExportError(
                f"--max-bytes {max_bytes} is below the {overhead + MIN_BODY_BYTES} bytes the "
                "header and truncation footer alone need",
                EXIT_REFUSED,
            )
        max_bytes = max(max_bytes - overhead, 1) if fits else max_bytes - overhead
    return frame(truncate(body, max_bytes), k.name, version, extra)
