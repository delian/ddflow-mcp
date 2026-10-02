"""``ddflow export``: project documents generated from the event log (decision D-export).

This package is the CORE: a registry of document kinds, a single-pass query index, a
framed deterministic rendering with a body digest, and a size cap. Writing to disk is
``write.py`` (B-export-write); the command and MCP tool are separate tasks. The core does
no I/O except reading the log in ``query.load`` and templates in ``registry.resolve_template``.

Plug-in contract for a document kind
------------------------------------
A kind is ONE new file, ``ddflow/services/export/kind_<name>.py``, plus one template,
``ddflow/templates/export/<name>.md.j2``. ``registry.discover()`` imports every ``kind_*``
module, so no shared list is edited. The module registers itself::

    from . import registry
    from .frame import one_line
    from .query import Query

    def _data(q: Query, f: registry.Filters) -> dict:
        rows = [{"id": t.id, "title": one_line(t.title)} for t in q.tasks("done")]
        return {"rows": rows[: f.limit or None]}

    registry.register(registry.DocKind(
        name="changelog",                      # == template stem
        default_target="CHANGELOG.md",         # repo-relative; the writer enforces safety
        data=_data,
        update_mode=registry.WHOLE,            # WHOLE | REGION | APPEND
        filters=frozenset({"since", "limit"}), # Filters fields this kind honours
        title="Release notes from completed tasks",
    ))

Rules a kind follows:

* ``data(query, filters)`` is PURE: it reads only the Query (``query.phases()``,
  ``tasks_of``, ``tasks_under``, ``tasks``, ``bugs``, ``decisions``, ``lessons``,
  ``sessions``, ``events_of``, ``last_event_id``, or ``query.state``). No log, git,
  filesystem, network, environment or clock access. It returns a mapping of plain values
  (str/int/list/dict) that the template iterates; pre-format text (shorten with your own
  helper) so the template is only loops and ``{{ var }}``.
* Determinism: every sequence has an explicit sort key with an id tie-break (use the
  Query accessors, which already do, or ``query.sorted_by``). Dates come from record or
  event data, never from "now". Same log, same bytes.
* Templates are Jinja2 with ``StrictUndefined`` (an undefined name raises, it never
  renders empty), resolved ``[export.<name>].template`` -> ``.ddflow/templates/export/``
  -> shipped, exactly as ``services/prompts.py`` does. Stay in the subset both renderers
  agree on: ``{{ var }}``, ``{% if %}``, ``{% for %}``.
* Filters: ``Filters`` is the single vocabulary (since, limit, status, phase, session).
  Declare the ones you honour; any other given filter is refused with exit 3.
* A failure (unreadable log, template error) raises ``ExportError`` with ``code``: 2 =
  could not run, 3 = refused. Never return an empty document to mean "I failed".

Entry points: ``query.load(root)`` -> ``Query``; ``registry.render_body`` /
``render_document(kind, query, filters, repo=..., max_bytes=...)``; ``frame.split`` /
``frame.hand_edited`` / ``frame.body_digest`` for the writer. The header is
``<!-- ddflow:generated doc=<kind> v=<version> body-sha256=<12 hex> -->`` over the body only.
"""

from . import frame, query, registry
from .frame import DEFAULT_MAX_BYTES, Header, body_digest, hand_edited, split
from .query import EXIT_REFUSED, EXIT_UNAVAILABLE, ExportError, Query, load
from .registry import (
    APPEND,
    REGION,
    WHOLE,
    DocKind,
    Filters,
    get,
    names,
    register,
    render_body,
    render_document,
)

__all__ = [
    "APPEND",
    "DEFAULT_MAX_BYTES",
    "EXIT_REFUSED",
    "EXIT_UNAVAILABLE",
    "REGION",
    "WHOLE",
    "DocKind",
    "ExportError",
    "Filters",
    "Header",
    "Query",
    "body_digest",
    "frame",
    "get",
    "hand_edited",
    "load",
    "names",
    "query",
    "register",
    "registry",
    "render_body",
    "render_document",
    "split",
]
