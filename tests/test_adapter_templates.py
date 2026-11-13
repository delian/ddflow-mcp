"""The `Templates` port: one set of cases against `JinjaRenderer` and the shipped
`FakeTemplates` (the strict stdlib renderer), so a test never imports Jinja2.

`CONFORMANCE` is read by the generic contract test (tests/test_ports_contract.py).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from ddflow.adapters.fakes.templates import FakeTemplates
from ddflow.adapters.templates import JinjaRenderer, TemplateError, Templates


def template_cases(io) -> None:
    assert isinstance(io, Templates)
    # a plain variable substitutes
    assert io.render("Hello {{ name }}!", name="world") == "Hello world!"
    # strict: an undefined name raises rather than rendering empty
    with pytest.raises(TemplateError):
        io.render("{{ missing }}")
    # render_from renders a file under the directory, strict too
    d = Path(tempfile.mkdtemp())
    (d / "greet.txt").write_text("Hello {{ person }}", encoding="utf-8")
    assert io.render_from(d, "greet.txt", person="world") == "Hello world"


CONFORMANCE = {
    "Templates": {
        "real": lambda monkeypatch: JinjaRenderer(),
        "cases": template_cases,
        "fake": lambda cls: cls(),
    }
}


def test_the_default_adapter_passes_the_templates_cases():
    template_cases(JinjaRenderer())


def test_the_fake_passes_the_templates_cases():
    template_cases(FakeTemplates())


def test_the_fake_renders_a_loop():
    fake = FakeTemplates()
    assert fake.render("{% for x in xs %}{{ x }}{% endfor %}", xs=[1, 2]) == "12"


def test_an_undefined_name_names_what_is_available():
    with pytest.raises(TemplateError) as err:
        FakeTemplates().render("{{ nope }}")
    assert "Available" in str(err.value)
