"""The committed log: which fields of which event kind are free text, and the per-event
redaction that applies them (D-unify 6).

`LOG_TEXT_FIELDS` and `LOG_VERBATIM_FIELDS` are DERIVED from the event-kind declarations
(`core.eventkinds.EVENT_KINDS`): a kind's `text` and `lookup` rows. A new kind declares
them in its own `ddflow/templates/events/<owner>.toml` and edits nothing here.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from typing import TYPE_CHECKING

from ..eventkinds import (
    EVENT_KINDS as _KINDS,
)
from ..eventkinds import (
    text_fields as _text_fields,
)
from ..eventkinds import (
    verbatim_fields as _verbatim_fields,
)
from .engine import _Pass
from .pattern import _HOST, _IPV4, _IPV6, _is_private, _loop

if TYPE_CHECKING:
    from .profiles import Redactor

#: Event kind -> the `data` fields that hold free text. `EventLog._write` runs these through
#: the `log` profile, so a secret, a LAN address, the machine's hostname or a home path in a
#: gate's output tail, a bug summary or a lesson is never committed (bug B5deba76d04).
#: A string field is redacted whole; a dict or list field is redacted leaf by leaf, whatever
#: its keys. These are the fields that are text even where their name is a known lookup name.
LOG_TEXT_FIELDS: Mapping[str, tuple[str, ...]] = _text_fields(_KINDS.values_list())

#: (kind, field) pairs that are compared or opened, though their name is not in
#: `LOG_VERBATIM_NAMES`, each with why (the kind's `lookup` rows).
LOG_VERBATIM_FIELDS: Mapping[tuple[str, str], str] = _verbatim_fields(_KINDS.values_list())

#: Field NAMES that are ids, digests, shas, paths or numbers something looks up by, in every
#: kind: written as given. The default is the other way round -- a field not listed here (or in
#: `LOG_VERBATIM_FIELDS`) is redacted, so a field the vocabulary does not know of (a `--note` on a
#: claim, a `--reason` on a link, a review's feedback) cannot be committed as typed.
LOG_VERBATIM_NAMES = frozenset(
    {
        "base", "branch", "digest", "duplicate_of", "extends", "fix_task", "fixes", "globs",
        "hash", "head", "id", "ids", "needs", "number", "parent", "path", "paths", "related",
        "resources", "sha", "subject", "supersedes", "tags", "tree", "url", "v", "worktree",
    }
)  # fmt: skip

#: Field names that are free text in every kind (the dedupe verdict quotes candidates).
LOG_TEXT_NAMES = frozenset({"dedupe"})

#: `(kind, field)` pairs that are compared byte-for-byte and are never a URL or a path, so
#: the network pass must skip them: a gated-command verb word is compared with the canonical
#: verb (`approvals.consume`), and one whose site ends in `.local` must not be rewritten into
#: `[REDACTED:host]` (bug B31f83a9d7b). Other verbatim fields that CAN carry a private host
#: (url, path, worktree, branch, tags, globs) keep the network pass (bug Ba2df74d558).
LOG_NO_NETWORK_FIELDS: frozenset[tuple[str, str]] = frozenset(
    {
        ("ask.requested", "verb"),
        ("ask.consumed", "verb"),
    }
)


def redact_network(text: str) -> str:
    """`text` with only its private addresses and private host names marked: the pass a
    lookup field gets, since an id or a sha has neither and a URL or path may (Ba2df74d558)."""
    p = _Pass(text)
    for pat, kind, ip_type in (
        (_IPV4, "ipv4", ipaddress.IPv4Address),
        (_IPV6, "ipv6", ipaddress.IPv6Address),
    ):
        # loopback is the default of a local endpoint: not a leak, and the repo guard allows it
        p.sub(pat, kind, keep=lambda m, t=ip_type: not _is_private(m.group(1), t) or _loop(m, t))
    p.sub(_HOST, "host")
    return p.text


def _map_leaves(value: object, fn) -> object:
    """``value`` with ``fn`` applied to every string leaf, whatever its key."""
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, Mapping):
        return {k: _map_leaves(v, fn) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_map_leaves(v, fn) for v in value]
    return value


def network_leaves(value: object) -> object:
    """``value`` with `redact_network` applied to every string leaf."""
    return _map_leaves(value, redact_network)


def redact_leaves(value: object, redactor: Redactor) -> object:
    """``value`` with every string leaf redacted, whatever its key: the keys of a text field
    come from data (gate names, `def` fields), and nothing looks a leaf up in the log."""
    return _map_leaves(value, lambda s: redactor.text(s).text)


def redact_event_data(
    kind: str, data: dict, redactor: Redactor, *, lookup_network: bool = True
) -> dict:
    """``data`` of an event of ``kind`` with its text redacted (a new dict).

    The declared text fields (`LOG_TEXT_FIELDS`) are redacted; so is every field that is not
    a known lookup (`LOG_VERBATIM_NAMES`, `LOG_VERBATIM_FIELDS`): unknown is not text-free.
    A lookup keeps its bytes except a private address or host in it, `lookup_network`
    (`[session].redact_lookup_network`; bug Ba2df74d558); a field in `LOG_NO_NETWORK_FIELDS`
    is compared byte-for-byte and keeps its bytes exactly."""
    text = LOG_TEXT_FIELDS.get(kind, ())
    out = {}
    for k, v in data.items():
        if k not in text and (k in LOG_VERBATIM_NAMES or (kind, k) in LOG_VERBATIM_FIELDS):
            if (kind, k) in LOG_NO_NETWORK_FIELDS:
                out[k] = v
            else:
                out[k] = network_leaves(v) if lookup_network else v
        else:
            out[k] = redact_leaves(v, redactor)
    return out
