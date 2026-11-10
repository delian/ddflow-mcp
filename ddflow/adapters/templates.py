"""The `Templates` port: the ONE place text templates are rendered.

`ddflow` takes no runtime dependencies -- that is what lets it install inside a sandbox
with no package index -- so Jinja2 can never be required. But a project that already has
it gets loops, conditionals and filters for free, and the shipped templates are written in
the subset both renderers agree on.

Two shapes, one seam:

- `render(text, **vars)` renders a template STRING;
- `render_from(directory, name, **vars)` renders a template FILE under `directory`.

The default adapter is `JinjaRenderer` (Jinja2 when importable, a strict stdlib renderer
otherwise); the fake `FakeTemplates` is that same strict stdlib renderer, so a test never
imports Jinja2. Strict about unknown variables: an undefined name raises rather than
rendering empty -- a prompt silently missing the diff it was supposed to carry is the
vacuous-review failure in template form.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .ports import port


class TemplateError(RuntimeError):
    """A template could not render: an undefined name, a syntax error, or a construct
    the fallback engine does not understand."""


@port
@runtime_checkable
class Templates(Protocol):
    """Render template text strictly. Home: `ddflow.adapters.templates` (`JinjaRenderer`);
    fake `FakeTemplates`."""

    def render(self, text: str, **vars: Any) -> str:
        """`text` with `vars` substituted; a missing name raises `TemplateError`."""

    def render_from(self, directory: Path | str, name: str, **vars: Any) -> str:
        """Template file `name` under `directory`, rendered with `vars`; strict."""


def _read(directory: Path | str, name: str) -> str:
    return (Path(directory) / name).read_text(encoding="utf-8")


class JinjaRenderer:
    """The default adapter: Jinja2 when importable, the strict stdlib renderer otherwise."""

    def render(self, text: str, **vars: Any) -> str:
        try:
            import jinja2
        except ImportError:
            return _render_stdlib(text, vars)
        # bandit B701: these templates render markdown/plain-text prompts, never HTML;
        # escaping would corrupt them.
        env = jinja2.Environment(  # nosec B701
            undefined=jinja2.StrictUndefined, trim_blocks=True, lstrip_blocks=True, autoescape=False
        )
        try:
            return env.from_string(text).render(**vars)
        except jinja2.TemplateError as exc:
            raise TemplateError(str(exc)) from exc

    def render_from(self, directory: Path | str, name: str, **vars: Any) -> str:
        try:
            import jinja2
        except ImportError:
            return _render_stdlib(_read(directory, name), vars)
        env = jinja2.Environment(  # nosec B701
            loader=jinja2.FileSystemLoader(str(directory)),
            undefined=jinja2.StrictUndefined,
            keep_trailing_newline=True,
        )
        try:
            return env.get_template(name).render(**vars)
        except jinja2.TemplateError as exc:
            raise TemplateError(str(exc)) from exc


#: The one stateless instance callers default to; a test passes `FakeTemplates` instead.
DEFAULT = JinjaRenderer()


# -- the strict stdlib renderer (the subset both renderers agree on) -------------------
# Moved here from `ddflow.services.prompts` so the false engine lives beside the port it
# is the fake stand-in for, and every render goes through the seam above.

#: One pass over the source finds every construct. Ordered so a comment wins over the
#: tags that may appear inside it.
_TOKEN = re.compile(
    r"(?P<comment>\{#.*?#\})"
    r"|\{%\s*(?P<tag>.*?)\s*%\}"
    r"|\{\{\s*(?P<expr>.*?)\s*\}\}",
    re.S,
)
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")

#: `for`, the loop variable, `in`, the sequence. Grammar, not a tunable.
_FOR_WORDS = 4


def _lookup(vars: dict[str, Any], dotted: str) -> Any:
    cur: Any = vars
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif hasattr(cur, part):
            cur = getattr(cur, part)
        else:
            raise TemplateError(
                f"template variable {dotted!r} is not defined. Available: {', '.join(sorted(vars))}"
            )
    return cur


def _tokenize(text: str) -> list[tuple[str, str]]:
    """Source -> a flat token list, applying Jinja's `trim_blocks` + `lstrip_blocks`."""
    out: list[tuple[str, str]] = []
    pos = 0
    for m in _TOKEN.finditer(text):
        chunk = text[pos : m.start()]
        pos = m.end()
        if m.group("expr") is None:
            cut = chunk.rfind("\n")
            if not chunk[cut + 1 :].strip():
                chunk = chunk[: cut + 1]  # lstrip_blocks; cut == -1 leaves ""
            if text[pos : pos + 1] == "\n":
                pos += 1  # trim_blocks
        if chunk:
            out.append(("text", chunk))
        if m.group("comment") is not None:
            continue
        out.append(
            ("block", m.group("tag")) if m.group("expr") is None else ("var", m.group("expr"))
        )
    if text[pos:]:
        out.append(("text", text[pos:]))
    return out


def _parse(tokens: list[tuple[str, str]]) -> list:
    """Tokens -> a tree, so a block is a SCOPE rather than a pair of markers."""
    root: list = []
    stack: list[tuple[str, list, list]] = [("root", root, root)]
    for kind, val in tokens:
        body = stack[-1][2]
        if kind in ("text", "var"):
            body.append((kind, val))
            continue
        words = val.split()
        head = words[0] if words else ""
        if head == "if":
            node = ["if", val[len("if") :].strip(), [], None]
            body.append(node)
            stack.append(("if", node, node[2]))
        elif head == "else":
            if stack[-1][0] != "if":
                raise TemplateError("{% else %} outside an {% if %}")
            node = stack[-1][1]
            node[3] = []
            stack[-1] = ("if", node, node[3])
        elif head == "endif":
            if stack[-1][0] != "if":
                raise TemplateError("{% endif %} with no open {% if %}")
            stack.pop()
        elif head == "for":
            if len(words) != _FOR_WORDS or words[2] != "in":
                raise TemplateError(f"only `{{% for x in seq %}}` is supported, not {val!r}")
            node = ["for", words[1], words[3], []]
            body.append(node)
            stack.append(("for", node, node[3]))
        elif head == "endfor":
            if stack[-1][0] != "for":
                raise TemplateError("{% endfor %} with no open {% for %}")
            stack.pop()
        else:
            raise TemplateError(
                f"the fallback template engine does not support {{% {val} %}}. "
                f"Install Jinja2 (`pip install jinja2`) for the full language."
            )
    if len(stack) != 1:
        raise TemplateError(f"unclosed {{% {stack[-1][0]} %}} block")
    return root


def _truthy(cond: str, vars: dict[str, Any]) -> bool:
    """`and`, `or`, `not` over names -- the subset the shipped templates use."""
    cond = cond.strip()
    if " or " in cond:
        return any(_truthy(p, vars) for p in cond.split(" or "))
    if " and " in cond:
        return all(_truthy(p, vars) for p in cond.split(" and "))
    if cond.startswith("not "):
        return not _truthy(cond[4:], vars)
    if not _NAME.fullmatch(cond):
        raise TemplateError(
            f"the fallback template engine cannot evaluate {cond!r}; it understands "
            f"names joined by `and`/`or`/`not`. Install Jinja2 for the full language."
        )
    return bool(_lookup(vars, cond))


def _emit(nodes: list, vars: dict[str, Any]) -> str:
    out: list[str] = []
    for node in nodes:
        kind = node[0]
        if kind == "text":
            out.append(node[1])
        elif kind == "var":
            expr = node[1]
            if not _NAME.fullmatch(expr):
                raise TemplateError(
                    f"the fallback template engine cannot evaluate {{{{ {expr} }}}}; it "
                    f"understands plain names. Install Jinja2 for the full language."
                )
            out.append(str(_lookup(vars, expr)))
        elif kind == "if":
            out.append(_emit(node[2] if _truthy(node[1], vars) else (node[3] or []), vars))
        elif kind == "for":
            for element in _lookup(vars, node[2]) or []:
                out.append(_emit(node[3], {**vars, node[1]: element}))
    return "".join(out)


def _render_stdlib(text: str, vars: dict[str, Any]) -> str:
    """Jinja's common subset, without Jinja, matched to the Jinja environment above."""
    if text.endswith("\r\n"):
        text = text[:-2]
    elif text.endswith("\n"):
        text = text[:-1]
    return _emit(_parse(_tokenize(text)), vars)
