"""The value kinds an argument may complete to, and how they are declared or inferred.

This module is import-light by contract (standard library only): the fast TAB path
runs it, and it must never import a surface, `ddflow.api` or the config layer. So the
value-kind vocabulary and the inference rules live here as pure functions over what an
argparse action already holds -- its ``dest``, ``choices``, ``metavar`` and whether it is
positional -- plus the one thing an argument may declare on purpose, ``complete``.

A kind is one short string:

* ``"none"``  -- free text; the argument must NOT complete (argparse offers no candidate).
* ``"enum"``  -- the argument's own ``choices``.
* ``"path"``  -- file paths.
* ``"dir"``   -- directory paths.
* ``"agent"`` -- agent / harness descriptor names.
* ``"gate"``  -- the configured pipeline's gate names.
* ``"state"`` -- item / gate state values.
* ``"kind"``  -- record-kind words.
* ``"id"``    -- record ids of ANY kind.
* ``"id:<kind>"`` -- record ids of one kind (``<kind>`` is a record-kind name from the
  record-kind registry; ``id:phase`` may be spelled ``phase``).

Inference (the default when ``complete`` is unset) resolves the same string from the
argument itself, so a parameter named after a record kind, a closed value set or a path
gains its completer from its declaration -- a NEW command, option, choice, record kind or
harness is completed with no completion code.

`_id_kinds` is the record-kind word list the inference knows. The item/record kinds are
seeded here; more (the data kinds `templates/kinds/*.toml`) register through
`declare_id_kind` — the completion engine's boot calls it once per record kind, so a
record kind added to the registry is inferable without editing this file.
"""

from __future__ import annotations

#: The fixed kinds that are not record ids.
FIXED = ("none", "enum", "path", "dir", "agent", "gate", "state", "kind")

#: Record-kind words an argument may be named after (item/record kinds of `core.model`
#: and the shipped data kinds). Extended by `declare_id_kind`.
_id_kinds: set[str] = {
    "item",
    "task",
    "phase",
    "bug",
    "lesson",
    "decision",
    "research",
    "session",
    "memory",
    "rule",
}

#: A parameter name that always means a directory (a default the argument may opt out of).
DIR_NAMES = {
    "repo",
    "dir",
    "dirs",
    "out",
    "workdir",
    "cwd",
    "output_dir",
}
#: A parameter name that always means a path (globs read and matched paths on disk).
PATH_NAMES = {"globs", "path", "paths", "file", "files"}

#: A parameter name that is a fixed (non-id) vocabulary kind.
NAME_KINDS = {
    "agent": "agent",
    "gate": "gate",
    "state": "state",
    "kind": "kind",
}

#: Metavars that name a directory or a file path outright.
DIR_METAVARS = {"DIR"}
PATH_METAVARS = {"PATH", "FILE"}


def declare_id_kind(name: str) -> None:
    """Record a record-kind word ``name`` into the inference's id vocabulary.

    Called once per kind by the completion engine's boot (which already imports the
    record-kind registry), so a newly added data kind is inferable with no edit here.
    """
    _id_kinds.add(name)


def id_kinds() -> frozenset[str]:
    """The record-kind words inference maps a parameter name to, as `id:<name>`."""
    return frozenset(_id_kinds)


def is_id(kind: str) -> bool:
    """Whether ``kind`` completes record ids (`id` or `id:<kind>`)."""
    return kind == "id" or kind.startswith("id:")


def record_kind(kind: str) -> str | None:
    """The record kind `id:<kind>` names, or None when ``kind`` is not an id form."""
    if kind.startswith("id:"):
        return kind[3:]
    if kind == "id":
        return None  # no kind narrowed
    return None


def normalize(kind: str) -> str:
    """A declared ``complete`` value spelled short as its canonical form.

    A record-kind word is spelled ``id:<word>`` (``phase`` -> ``id:phase``, "bug" ->
    ``id:bug``); a fixed kind or an ``id:`` form passes through whole.
    """
    kind = kind.strip()
    if kind in _id_kinds:
        return f"id:{kind}"
    return kind


def infer(
    name: str,
    *,
    choices: tuple | list | None = None,
    metavar: str | None = None,
    positional: bool = False,
    record_kind: str | None = None,
) -> str:
    """The completion kind of an argument, declared nowhere else on it.

    An explicit ``Param.complete`` is NOT passed here; the caller resolves that first (it
    wins, verbatim). This answers only the DEFAULT:

    * ``choices`` -> ``enum``;
    * a name that spells a record kind -> ``id:<name>``;
    * a name that spells a fixed vocabulary kind -> that kind;
    * a positional named ``id`` under a record verb -> ``id:<record kind>``;
       a bare ``id`` positional elsewhere -> ``id``;
    * a directory / file-path name, or a ``DIR`` metavar -> ``dir``, a ``PATH``/``FILE``
       metavar -> ``path`` (the argument's declared value shape);
    * anything else -> ``none``.

    There is no ``path`` JSON type on `Param`, so a path argument says so through its
    name or its metavar, not through ``cli_type``.
    """
    if choices is not None:
        return "enum"
    # A name that IS the generic id word (an `id` positional under a command, or a
    # positional that takes many ids at once): a record verb narrows it to its own kind.
    if name == "id" or name == "ids":
        return f"id:{record_kind}" if positional and record_kind else "id"
    # A name spelled ``<kind>_id`` (``decision_id``, ``research_id``): the kind's ids.
    if name.endswith("_id"):
        stem = name[:-3]
        return f"id:{stem}" if stem in _id_kinds else "id"
    # A name that spells a fixed vocabulary kind, or a record kind, is more specific than
    # a bare ``ID`` metavar: `agent`/`bug` name the vocabulary, ``ID`` alone is the record-id
    # fallback for an id call whose name says nothing specific (``--extends ID``).
    if name in NAME_KINDS:
        return NAME_KINDS[name]
    if name in _id_kinds:
        return f"id:{name}"
    # A flag whose metavar literally says ``ID`` takes a record id when its name does not.
    if metavar == "ID":
        return "id"
    if name in DIR_NAMES or metavar in DIR_METAVARS:
        return "dir"
    if name in PATH_NAMES or metavar in PATH_METAVARS:
        return "path"
    return "none"


#: How a name would resolve, for a ratchet to distinguish a completed value from free text.
def looks_like_id(name: str, *, metavar: str | None = None) -> bool:
    """An id-like parameter name: a record item/kind word, the generic ``id``/``<kind>_id``,
    or a metavar that literally says ``ID``."""
    if metavar == "ID":
        return True
    if name in ("id", "ids") or name.endswith("_id"):
        return True
    return name in _id_kinds
