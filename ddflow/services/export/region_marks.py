"""The region marker text and the ``region`` template call (B-tpl-regions, D-doc-regions).

Kept free of the writer so the template registry can import it: a template writes::

    {% call region("summary") %}
    ...generated text...
    {% endcall %}

and the render carries ``<!-- ddflow:begin <doc>/summary sha=... -->`` around it.
"""

from __future__ import annotations

from typing import Any

from . import frame as F

DOC_KEY = "_region_doc"  # render context key: the document kind a render is for


def render_region(doc: str, name: str, body: str) -> str:
    """The marked region for ``body``, ending in a newline."""
    body = F.normalize(body)
    return (
        f"<!-- ddflow:begin {doc}/{name} sha={F.body_digest(body)} -->\n{body}"
        f"<!-- ddflow:end {doc}/{name} -->\n"
    )


def region(context: Any, name: str, caller=None) -> str:  # type: ignore[no-untyped-def]
    """``{% call region("name") %}body{% endcall %}``: the body inside its markers."""
    import jinja2

    doc = context.get(DOC_KEY) or ""
    if not doc:
        raise jinja2.TemplateRuntimeError("region() needs the document kind of the render")
    if caller is None:
        raise jinja2.TemplateRuntimeError(f"region({name!r}) is used as {{% call %}} with a body")
    return render_region(doc, str(name), str(caller()))


def jinja_region() -> Any:
    """``region`` as a template global: jinja passes it the render context. Built on use, so
    importing this module does not import jinja2."""
    import jinja2

    return jinja2.pass_context(region)
