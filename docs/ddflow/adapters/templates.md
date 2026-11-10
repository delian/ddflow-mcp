# Adapter family `templates`

Port: **Templates**, declared in `ddflow/adapters/templates.py` and marked `@port`.

The one place text templates are rendered. `render(text, **vars)` renders a template string; `render_from(directory, name, **vars)` renders a template file. Strict about unknown variables: an undefined name raises `TemplateError` rather than rendering empty.

- **Default adapter:** `JinjaRenderer` in `ddflow.adapters.templates`; `DEFAULT` is the shared instance. Jinja2 when importable, a strict stdlib renderer otherwise (the subset both engines agree on: `{{ var }}`, `{% if %}` / `{% else %}` / `{% endif %}`, `{% for %}` / `{% endfor %}`, comments).
- **Fake:** `FakeTemplates` in `ddflow/adapters/fakes/templates.py`: the strict stdlib renderer, importing no Jinja2.
- **Conformance cases:** tests/test_adapters_templates.py.
- **Ratchet:** direct `jinja2` imports in `ddflow/services` may not grow; render through this port.

The pattern is in [README.md](README.md).
