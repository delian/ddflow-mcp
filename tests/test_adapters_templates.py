"""The `Templates` port: the same cases against the default adapter and the fake."""

from __future__ import annotations

import pytest

from ddflow.adapters import templates as T
from ddflow.adapters.fakes import FAKES
from ddflow.adapters.fakes.templates import FakeTemplates
from ddflow.adapters.templates import Templates, TemplateError


def templates_conformance(io) -> None:
    """What every Templates renderer does, real or fake."""
    # A bare variable, an if/else, and a for loop agree across engines.
    assert io.render("hello {{ name }}", name="world") == "hello world"
    assert io.render("{% if ok %}y{% else %}n{% endif %}", ok=True) == "y"
    assert io.render("{% for x in xs %}{{ x }}{% endfor %}", xs=[1, 2]) == "12"
    # A missing name raises: never renders empty.
    with pytest.raises(TemplateError):
        io.render("{{ nope }}")


CONFORMANCE = {
    "Templates": {
        "real": lambda monkeypatch: T.DEFAULT,
        "cases": templates_conformance,
        "fake": lambda cls: cls(),
    }
}


def test_the_adapter_and_the_fake_satisfy_the_port():
    assert isinstance(T.DEFAULT, Templates) and isinstance(FakeTemplates(), Templates)
    assert FAKES["Templates"] is FakeTemplates


def test_the_default_renderer_renders_a_file(tmp_path):
    (tmp_path / "t.md.j2").write_text("hi {{ who }}\n", encoding="utf-8")
    assert T.DEFAULT.render_from(tmp_path, "t.md.j2", who="there") == "hi there\n"


def test_the_default_renderer_is_impervious_to_a_missing_name(tmp_path):
    (tmp_path / "t.md.j2").write_text("{{ missing }}", encoding="utf-8")
    with pytest.raises(TemplateError):
        T.DEFAULT.render_from(tmp_path, "t.md.j2")
