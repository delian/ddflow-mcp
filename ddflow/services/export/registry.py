"""The registry of document kinds, and how a kind turns a Query into a document.

See the package docstring (``ddflow.services.export``) for the plug-in contract.
"""

from __future__ import annotations

import ctypes
import importlib
import pkgutil
import re
import textwrap
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import jinja2
from jinja2.sandbox import SandboxedEnvironment

from ...core.digest import content_digest
from ...core.version import running as running_version
from ...infra.paths import templates_dir
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
    tag: str = ""  # one tag (lessons, decisions)

    def given(self) -> set[str]:
        """Names set to something other than their default."""
        return {f.name for f in fields(self) if getattr(self, f.name) != f.default}


@dataclass(frozen=True)
class DocKind:
    name: str  # also the template stem: templates/export/<name>.md.j2
    default_target: str  # repo-relative path the writer uses unless told otherwise
    data: Callable[
        [Query, Filters], Mapping[str, Any]
    ]  # pure over the Query: no clock; only Query.repo's config may be read
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
        if name == "replay":
            raise ExportError(
                "replay is not an export kind: it carries private paths and addresses "
                "and is built only for the machine that asks (use `ddflow replay`)",
                EXIT_REFUSED,
            ) from None
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


def _flanked(m: re.Match[str]) -> str:
    """Escape an emphasis marker unless it is a lone one between spaces (``a * b``) or,
    for ``_``, sits INSIDE a word (``run_nemo_run``: CommonMark does not read an intraword
    underscore as emphasis, so there is nothing to protect)."""
    text, i = m.string, m.start()
    prev = text[i - 1] if i else " "
    nxt = text[i + 1] if i + 1 < len(text) else " "
    if prev.isspace() and nxt.isspace():
        return m.group(0)
    if m.group(0) == "_" and prev.isalnum() and nxt.isalnum():
        return m.group(0)
    return "\\" + m.group(0)


def md_escape(value: object) -> str:
    """Escape what would change how free text RENDERS, and fold it onto one line.

    Not every punctuation mark: an identifier (``run_nemo_run``) or a bracketed id reads
    as itself, and a backslash before each of them is noise in the document's source
    (B3da43a71bb). Escaped: backslashes, backticks, pipes (they split a table cell),
    emphasis markers (``*``, and ``_`` except inside a word), ``~~``, a link opener
    (``](`` / ``][``), a ``<`` that opens a tag, comment, autolink or declaration, an
    ``&`` that opens an entity, and a line-start list, heading, quote or rule marker.
    """
    s = " ".join(str(value).split())
    s = re.sub(r"([\\`|])", r"\\\1", s)
    s = re.sub(r"[*_]", _flanked, s)
    s = s.replace("~~", "\\~\\~")
    s = re.sub(r"\](?=[(\[])", r"\\]", s)
    s = re.sub(r"<(?=[A-Za-z/!?])", r"\\<", s)
    s = re.sub(r"&(?=#?\w+;)", r"\\&", s)
    return re.sub(r"^(?:([#>+*])|(-)(?=[- ])|(\d+)([.)])(?= ))", _line_start, s)


def _line_start(m: re.Match[str]) -> str:
    if m.group(3):
        return m.group(3) + "\\" + m.group(4)
    return "\\" + m.group(0)


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
    w, t = _cap_width(width), int(total)  # type: ignore[call-overload]
    n = 0 if t <= 0 else max(0, min(w, round(w * int(done) / t)))  # type: ignore[call-overload]
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


#: Ceilings for what one template expression may build. Python runs `'x' * 10**10` in C, in
#: one allocation, past any deadline, so the sandbox refuses it BEFORE it runs.
MAX_SEQUENCE = 1_000_000  # items/characters from one `*`, `center`, `indent`, width spec
MAX_EXPONENT = 10_000  # `a ** b`
MAX_RESULT_BITS = 4_000_000  # size of an integer `a ** b`
MAX_OUTPUT_BYTES = 16_000_000  # the rendered document itself

# A printf width/precision past MAX_SEQUENCE or `*`; a str.format width/precision past it, or
# a nested `{:{}}`. Anchored on the `%`/`{...:` so ordinary text and markdown (`**{}**`,
# "ticket 1234567") never match.
_WIDTH = re.compile(
    r"%[-+ #0]*(?:\d{7,}|\*)"
    r"|%[-+ #0]*\d*\.(?:\d{7,}|\*)"
    r"|\{[^{}]*:(?:[^{}]?[<>^=])?[-+ ]?#?0?\d{7,}"
    r"|\{[^{}]*:[^{}]*\.\d{7,}"
    r"|\{[^{}]*:[^{}]*\{"
)


def _too_big(what: str) -> OverflowError:
    return OverflowError(f"{what} is larger than the template sandbox allows")


def _cap_width(width: object) -> int:
    n = int(width)  # type: ignore[call-overload]
    if n > MAX_SEQUENCE:
        raise _too_big(f"width {n}")
    return n


def _format(value: object, *args: object, **kwargs: object) -> str:
    text = str(value)
    if _WIDTH.search(text):
        raise _too_big("format width")
    return text % (kwargs or args)  # type: ignore[operator]


def _center(value: object, width: int = 80) -> str:
    return str(value).center(_cap_width(width))


def _indent(value: object, width: int = 4, first: bool = False, blank: bool = False) -> str:
    n = _cap_width(width)
    lines = str(value).splitlines(keepends=True)
    if n * len(lines) > MAX_SEQUENCE:  # the padded result, not just the pad, is bounded
        raise _too_big("indented text")
    pad = " " * n
    out = [pad + ln if (i or first) and (blank or ln.strip()) else ln for i, ln in enumerate(lines)]
    return "".join(out)


class _Sandbox(SandboxedEnvironment):
    """Jinja's sandbox, plus ceilings on the operators and filters that allocate by size."""

    intercepted_binops = frozenset({"*", "**", "%"})

    def call_binop(self, context, operator, left, right):  # type: ignore[no-untyped-def]
        if operator == "*":
            for seq, n in ((left, right), (right, left)):
                if isinstance(seq, (str, bytes, list, tuple)) and isinstance(n, int):
                    if len(seq) * max(n, 0) > MAX_SEQUENCE:
                        raise _too_big(f"{type(seq).__name__} repetition")
        elif operator == "**":
            if isinstance(right, (int, float)) and abs(right) > MAX_EXPONENT:
                raise _too_big(f"exponent {right}")
            if isinstance(left, int) and isinstance(right, int) and right > 0:
                # `(10**10000)**10000` is two small exponents and a 10**8-digit result
                if left.bit_length() * right > MAX_RESULT_BITS:
                    raise _too_big("power result")
        elif operator == "%" and isinstance(left, str) and _WIDTH.search(left):
            raise _too_big("format width")
        return super().call_binop(context, operator, left, right)

    _SIZED_METHODS = frozenset({"center", "ljust", "rjust", "zfill", "expandtabs"})

    def call(self, __context, __obj, *args, **kwargs):  # type: ignore[no-untyped-def]
        owner, name = getattr(__obj, "__self__", None), getattr(__obj, "__name__", "")
        if isinstance(owner, str):
            if name in self._SIZED_METHODS:
                for a in (*args, *kwargs.values()):
                    if isinstance(a, int) and a > MAX_SEQUENCE:
                        raise _too_big(f"width {a}")
        return super().call(__context, __obj, *args, **kwargs)

    def wrap_str_format(self, value):  # type: ignore[no-untyped-def]
        # `'{:>9999999999}'.format(1)`: the width is in the receiver string, so check it
        # before the sandbox's own wrapper formats anything.
        owner = getattr(value, "__self__", None)
        if (
            getattr(value, "__name__", "") in ("format", "format_map")
            and isinstance(owner, str)
            and _WIDTH.search(owner)
        ):
            raise _too_big("format width")
        return super().wrap_str_format(value)


def _environment() -> SandboxedEnvironment:
    # bandit B701: Markdown, never HTML; escaping would corrupt the output. The sandbox is
    # the point: a template may come from a cloned repository.
    env = _Sandbox(  # nosec B701
        undefined=jinja2.StrictUndefined, trim_blocks=True, lstrip_blocks=True, autoescape=False
    )
    env.globals.pop("lipsum", None)  # backed by `random`: a template must be deterministic
    env.filters.pop("random", None)
    env.filters.update(FILTERS)
    env.filters["center"] = _center  # the built-ins allocate `width` bytes unchecked
    env.filters["indent"] = _indent
    env.filters["format"] = _format
    return env


def _render_capped(env: SandboxedEnvironment, text: str, ctx: dict[str, Any]) -> str:
    """Render, stopping with an error once the output passes ``MAX_OUTPUT_BYTES``."""
    out: list[str] = []
    size = 0
    for chunk in env.from_string(text).generate(**ctx):
        size += len(chunk)
        if size > MAX_OUTPUT_BYTES:
            raise _too_big("rendered output")
        out.append(chunk)
    return "".join(out)


def _run_limited(fn: Callable[[], str], seconds: float) -> str:
    """Run ``fn`` in a helper thread; past ``seconds`` raise ``TimeoutError`` INTO it.

    Works from any thread  and touches no process-wide
    state (no signal handler, no itimer another component might be using). A compiled
    template is pure Python bytecode, so the asynchronous exception lands inside the
    runaway loop and ends it; the caller always gets an error, never a hang.

    A limit, stated plainly: this and the size ceilings above are BEST-EFFORT resource
    containment, not a guarantee. The sandbox's firm promise is isolation (no attribute
    escape, no file, process or import access); a template that finds a native call big
    enough to outlast the deadline is reported as a timeout, but its thread may keep running.
    """
    if seconds <= 0:
        return fn()
    box: dict[str, Any] = {}

    def work() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:
            box["error"] = exc

    t = threading.Thread(target=work, name="ddflow-export-render", daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        tid = ctypes.c_ulong(t.ident or 0)
        hit = ctypes.pythonapi.PyThreadState_SetAsyncExc(tid, ctypes.py_object(TimeoutError))
        if hit > 1:  # more than one thread state matched: undo, never hit a bystander
            ctypes.pythonapi.PyThreadState_SetAsyncExc(tid, None)
        t.join(2.0)
        if "value" in box:  # it finished in the window before the interrupt landed
            return box["value"]
        stuck = " and could not be stopped (a native call)" if t.is_alive() else ""
        raise TimeoutError(f"template ran longer than {seconds:g}s{stuck}")
    if "error" in box:
        raise box["error"]
    return box["value"]


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
    if not isinstance(data, Mapping):
        raise ExportError(f"{where}: template data must be a mapping, not {type(data).__name__}")
    try:
        ctx = plain(dict(data))
    except RecursionError:
        raise ExportError(
            f"{where}: template data is nested too deeply or refers to itself"
        ) from None
    ctx["schema_version"] = schema_version
    try:
        env = _environment()
        return normalize(_run_limited(lambda: _render_capped(env, text, ctx), timeout_s))
    except jinja2.TemplateSyntaxError as exc:
        raise ExportError(f"{where}:{exc.lineno}: {exc.message}") from exc
    except jinja2.TemplateError as exc:  # undefined variable, SecurityError, ...
        line = _template_line(exc)
        raise ExportError(f"{where}{':' + str(line) if line else ''}: {exc}") from exc
    except Exception as exc:
        # An ordinary method call can raise anything (`[].pop()` -> IndexError); none of it
        # may escape as a traceback, and none of it may leave a half-rendered document.
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
    return content_digest(text, length=12)


def shipped_digest(kind: str, builtin: Path | None = None) -> str:
    """The digest of the SHIPPED template for ``kind`` (never an override).

    An eject records it next to the copy; ``validate``/doctor compare it with this to say
    "the shipped default changed since you ejected".
    """
    return template_digest(shipped_template(kind, builtin).text)


def shipped_template(kind: str, builtin: Path | None = None) -> Template:
    """The shipped default for ``kind``, ignoring any override (what eject copies)."""
    if builtin is None:
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


def render_document(  # noqa: PLR0913 -- the document kind's whole vocabulary: filters, cap, frame and the body hook
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
    post: Callable[[str], tuple[str, dict[str, str]]] | None = None,
) -> str:
    """The full framed document (header + body), capped to ``max_bytes`` (0 = uncapped).

    ``max_bytes`` bounds the WHOLE document: the header's length is reserved before the
    body is truncated; a cap too small for header + footer is refused (exit 3) unless the
    whole document already fits in it. Truncation happens BEFORE framing, so the header digest always
    matches the bytes shown and ``frame.hand_edited`` is False for any output. Pure: same
    inputs, same bytes.

    ``post(body) -> (body, header attributes)`` runs on the whole rendered body BEFORE it is
    truncated and digested (redaction, B-export-redact-fence).
    """
    k = get(kind) if isinstance(kind, str) else kind
    body = render_body(
        k, query, filters, repo=repo, overrides=overrides, builtin=builtin, template=template
    )
    if post is not None:
        body, more = post(body)
        extra = {**(extra or {}), **more}
    if not version:
        version = running_version()
    try:
        overhead = len(frame("", k.name, version, extra).encode("utf-8"))
    except ValueError as exc:  # an `extra` header attribute the reader could not parse back
        raise ExportError(str(exc), EXIT_REFUSED) from exc
    if max_bytes > 0:
        fits = overhead + len(normalize(body).encode("utf-8")) <= max_bytes
        if not fits and max_bytes < overhead + MIN_BODY_BYTES:
            raise ExportError(
                f"--max-bytes {max_bytes} is below the {overhead + MIN_BODY_BYTES} bytes the "
                "header and truncation footer alone need",
                EXIT_REFUSED,
            )
        max_bytes = max(max_bytes - overhead, 1) if fits else max_bytes - overhead
    return frame(truncate(body, max_bytes), k.name, version, extra)  # `extra` validated above
