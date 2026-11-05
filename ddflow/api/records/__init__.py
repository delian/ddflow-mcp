"""One record surface, the API half: a `RecordKind` descriptor and the verbs it gets.

About twelve families (rules, skills, agents, schedules, triggers, doc types, quotas,
harnesses, ids ...) each planned their own list / show / add / edit / remove / search /
revise, with its own filter flags, its own duplicate check, its own regex search and its
own unbounded payloads (R-unify; D-unify 4). A family declares a `RecordKind` instead, and
this package is what answers for it:

* ``record_list``    filtered, sorted, bounded rows (columns the descriptor names);
* ``record_show``    one record whole, with its history cut to the newest entries;
* ``record_add``     a NEW record, through the add-time duplicate check (D-no-duplicates);
* ``record_edit``    some fields of an active record, the rest kept;
* ``record_remove``  retire it, with a reason (kept, with its history);
* ``record_search``  ranked, exact or bounded-regex search over the record's own text;
* ``record_revise``  replace the whole record, with a reason, bringing a retired one back.

History, supersede, merge, split and restore are `ddflow.api.record_lifecycle`'s; a kind offers
or declines each, with a reason (``declined``).

The records are definitions (`core.defs`, `api.defs`): the descriptor's ``def_kind`` is the
kind they are filed under, so history, provenance, digest and the duplicate check come
with them. A family whose records live elsewhere (a quota is not a definition) gives
``ops`` instead: a verb found there is called with ``(repo, kind, **arguments)`` and
answers an `Outcome`, and the surfaces generated from the descriptor stay the same.

Every payload is bounded: a list or a search shows at most ``limit`` rows (default
`DEFAULT_LIMIT`, at most `MAX_LIMIT`) and says ``total`` and ``truncated``; a long text in a
row is cut (`CELL_MAX`); a record's history shows its newest `HISTORY_SHOWN` entries.
Counts are exact.

The single module was split into one module per concern in this package; every name it
exposed is re-exported here, so ``from ddflow.api.records import ...`` and
``import ddflow.api.records as R`` keep working.

Pure of surfaces: `ddflow.surfaces.records` generates the CLI verbs and the MCP tool from
a `RecordKind`.
"""

from __future__ import annotations

import sys

from ..record_kind_data import load_all
from ._kind import (  # noqa: F401
    _CUT,
    CELL_ITEMS,
    CELL_MAX,
    DEFAULT_LIMIT,
    FIELD_TYPES,
    HISTORY_SHOWN,
    KINDS,
    MAX_LIMIT,
    MAX_SCAN,
    MODES,
    SENSITIVITY_FIELD,
    VERBS,
    ExportSpec,
    FeedSpec,
    FieldSpec,
    RecordKind,
    _placeholders,
    _text,
    declare,
    forget,
)
from ._resources import (  # noqa: F401
    RESOURCE_PREFIX,
    RESOURCE_ROWS,
    RESOURCE_TEXT,
    _resource_kinds,
    _resource_text,
    record_resource_listing,
    record_resource_read,
)
from ._verbs import (  # noqa: F401
    _BRING_BACK,
    _absent,
    _as,
    _body,
    _cell,
    _checked,
    _event,
    _exists,
    _field_problem,
    _filter_problem,
    _limit,
    _matches,
    _named,
    _page,
    _pre,
    _records,
    _row,
    _scored,
    record_add,
    record_edit,
    record_list,
    record_remove,
    record_revise,
    record_search,
    record_show,
)

#: The shipped kinds are data: ``ddflow/templates/kinds/*.toml`` (the rule kind first).
RULE = load_all(sys.modules[__name__])[0]
