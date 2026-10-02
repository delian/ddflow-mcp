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
* FORMAT IS DDFLOW'S, NOT THE AGENT'S (D-export-templates). The template receives ONLY
  the plain data ``data()`` returns (dict/list/str/int/float/bool/None; tuples become
  lists; anything else is refused naming the path), plus ``schema_version`` (the kind's
  ``DocKind.schema_version``: ddflow only ADDS fields within a version) and the filters
  ``md_escape``, ``wrap(width)``, ``date``, ``truncate(limit)``, ``bar(done, total,
  width)`` beside Jinja's built-ins. It runs in ``jinja2.sandbox.SandboxedEnvironment``
  (StrictUndefined, trim_blocks, lstrip_blocks, no autoescape) with a time limit, because a
  template may come from a cloned repository: no ``__class__``/``__globals__`` walk, no
  ``open``, no imports. Any template failure is ``ExportError`` exit 2 naming file and line.
* Templates resolve ``[export.<name>].template`` -> ``.ddflow/templates/export/<name>.md.j2``
  -> shipped ``ddflow/templates/export/<name>.md.j2`` (same order as ``services/prompts.py``;
  ``registry.resolve_template``). ``registry.shipped_digest(kind)`` is the digest an eject
  records and a validate compares, to say the shipped default moved. The
  ``<!-- ddflow:generated ... -->`` header and body digest are added by ddflow AROUND the
  template output, so a template cannot remove hand-edit protection.
* Filters: ``Filters`` is the single vocabulary (since, limit, status, phase, session).
  Declare the ones you honour; any other given filter is refused with exit 3.
* A failure (unreadable log, template error) raises ``ExportError`` with ``code``: 2 =
  could not run, 3 = refused. Never return an empty document to mean "I failed".

Entry points: ``query.load(root)`` -> ``Query``; ``registry.render_body`` /
``render_document(kind, query, filters, repo=..., max_bytes=..., template=...)``;
``registry.resolve_template`` / ``render`` / ``shipped_digest`` / ``shipped_template``; ``frame.split`` /
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
    render,
    render_body,
    render_document,
    resolve_template,
    shipped_digest,
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
    "render",
    "render_body",
    "render_document",
    "resolve_template",
    "shipped_digest",
    "split",
]
