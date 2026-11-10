"""`FakeTemplates`: the strict stdlib renderer as a `Templates`, importing no Jinja2."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..templates import _render_stdlib
from . import fake


@fake
class FakeTemplates:
    """A `Templates` backed by the strict stdlib renderer (`{{ var }}`, `{% if %}` /
    `{% else %}` / `{% endif %}`, `{% for %}` / `{% endfor %}`, `{# #}`); anything else
    raises `TemplateError`."""

    def render(self, text: str, **vars: Any) -> str:
        return _render_stdlib(text, vars)

    def render_from(self, directory: Path | str, name: str, **vars: Any) -> str:
        return _render_stdlib((Path(directory) / name).read_text(encoding="utf-8"), vars)
