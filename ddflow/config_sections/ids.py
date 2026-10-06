"""The `[ids]` section: one template per record kind (decision D-id-schemes-final).

Re-exported from `ddflow.config`, which assembles `Config` from every section. The
defaults reproduce the ids ddflow has always minted, so a project that sets nothing sees
no change and an existing log replays byte-identically; a recorded id is never renamed.
Rendering lives in `core/ids.py`; the id service that allocates `{seq}` and checks taken
keys is B-id-generator's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from typing import Any

from ._docs import _doc

#: The tokens a template may use. `{seq}`, `{hash}`, or `{time}` with `{pid}` make an id
#: unique; `{digest}` makes a template STABLE (the same content maps to the same id).
ID_TOKENS = (
    "prefix", "seq", "date", "slug", "hash", "parent", "phase", "env", "user-text",
    "time", "pid", "digest",
)  # fmt: skip
#: The only tokens a stable template may use: fixed by the record's content and kind.
ID_STABLE_TOKENS = frozenset({"prefix", "slug", "parent", "phase", "user-text", "digest"})
#: Characters an id may hold outside its tokens: it is a file name, a branch name and a
#: glob token, so ASCII letters, digits, `.`, `_` and `-` only.
ID_CHAR = re.compile(r"[A-Za-z0-9._-]")
#: The letter `{prefix}` stands for, per kind: the letters every existing log holds.
ID_PREFIXES: dict[str, str] = {
    "bug": "B",
    "lesson": "L",
    "research": "R",
    "decision": "D",
    "memory": "M",
    "job": "J",
}
#: Kinds whose ids the CALLER names (an imported file's own ids, or a slug of a heading):
#: the importer makes them unique (`-2`, `-3`) and a clash between clones is CONTESTED,
#: as for any named item, so their templates need no uniqueness token.
ID_NAMED_KINDS = frozenset({"imported_phase", "imported_task"})
#: Kinds minted once per parent record: `{parent}` is their uniqueness (a bug's own fix
#: task is `fix-<bug>`; a follow-up takes `{seq}`).
ID_PER_PARENT_KINDS = frozenset({"fix_task"})


@dataclass
class IdsConfig:
    """`[ids]`: the template each record kind's id is minted from."""

    bug: str = "{prefix}{hash}"
    lesson: str = "{prefix}{hash}"
    research: str = "{prefix}{hash}"
    decision: str = "{prefix}{hash}"
    memory: str = "{prefix}{hash}"
    job: str = "{prefix}{hash}"
    session: str = "s{time}-{pid}"
    fix_task: str = "fix-{parent}"
    fix_task_followup: str = "fix-{parent}-{seq}"
    promotion: str = "promote-{env}-{seq}"
    ci_bug: str = "Bci-{slug}-{digest}"
    split_child: str = "{parent}.{seq}"
    imported_phase: str = "{user-text}"
    imported_task: str = "{phase}.{slug}"


#: Every kind, in declaration order.
ID_KINDS: tuple[str, ...] = tuple(f.name for f in fields(IdsConfig))


def id_problem(name: Any) -> str:
    """Why `name` cannot be an id (or a fixed id such as `[bugs].phase`), or ""."""
    if not isinstance(name, str) or not name:
        return "must be a non-empty name"
    if bad := sorted({c for c in name if not ID_CHAR.fullmatch(c)}):
        return (
            f"holds {bad[0]!r}: an id is a file name, a branch name and a glob token, so "
            "only ASCII letters, digits, '.', '_' and '-' may appear"
        )
    if name[0] in ".-" or name.endswith(".") or ".." in name or name.endswith(".lock"):
        return (
            "must not start with '.' or '-', end with '.' or '.lock', or hold '..' (git "
            "refuses such branch names)"
        )
    return ""


def id_template_problem(kind: str, template: Any) -> str:
    """Why `template` cannot mint `kind`'s ids, or "" (D-id-schemes-final)."""
    if not isinstance(template, str) or not template:
        return "must be a non-empty template string"
    tokens = re.findall(r"\{([^{}]*)\}", template)
    literal = re.sub(r"\{[^{}]*\}", "", template)
    if bad := [t for t in tokens if t not in ID_TOKENS]:
        known = ", ".join(f"{{{t}}}" for t in ID_TOKENS)
        return f"unknown token {{{bad[0]}}}; the tokens are {known}"
    if "{" in literal or "}" in literal:
        return "has an unbalanced brace"
    if bad := sorted({c for c in literal if not ID_CHAR.fullmatch(c)}):
        return (
            f"holds {bad[0]!r}: an id is a file name, a branch name and a glob token, so "
            "only ASCII letters, digits, '.', '_' and '-' may appear outside the tokens"
        )
    shape = re.sub(r"\{[^{}]*\}", "x", template)  # each token stands for some text
    if shape[0] in ".-" or shape.endswith(".") or ".." in shape or shape.endswith(".lock"):
        return (
            "must not start with '.' or '-', end with '.' or '.lock', or hold '..' (git "
            "refuses such branch names)"
        )
    if "prefix" in tokens and kind not in ID_PREFIXES:
        return f"uses {{prefix}}, which only {', '.join(ID_PREFIXES)} ids have"
    if "digest" in tokens:
        if bad := [t for t in tokens if t not in ID_STABLE_TOKENS]:
            return (
                f"is stable (it holds {{digest}}), so it may not hold {{{bad[0]}}}, which "
                "changes between filings: a stable template maps the same content to the "
                "same id"
            )
        return ""
    if kind in ID_NAMED_KINDS:
        return ""
    unique = {"seq", "hash"} & set(tokens) or {"time", "pid"} <= set(tokens)
    if kind in ID_PER_PARENT_KINDS and "parent" in tokens:
        unique = True
    if not unique:
        return (
            "needs a source of uniqueness: {seq}, {hash}, or {time} with {pid} -- or "
            "{digest}, to be a stable template"
        )
    return ""


_RULES = (
    " Tokens: {prefix} (the kind's letter), {seq} (next free number), {date}, {time}, "
    "{pid}, {slug}, {hash} (salted, ten hex digits), {parent}, {phase}, {env}, "
    "{user-text}, {digest} (stable content digest). A template needs {seq}, {hash} or "
    "{time} with {pid}; one holding {digest} is stable and may hold only content "
    "tokens. Characters outside tokens: ASCII letters, digits, '.', '_', '-'. Applies "
    "to records created afterwards; a recorded id is never renamed. An invalid template "
    "in a file is warned about, reported by `doctor`, and the default stays in effect; "
    "`config --set` refuses it (exit 3)."
)
_KIND_DOCS = {
    "bug": "Bug ids. Default {prefix}{hash}: B and ten hex digits, as ever (B9c56de9d58).",
    "lesson": "Lesson ids. Default {prefix}{hash}: L and ten hex digits.",
    "research": "Research note ids. Default {prefix}{hash}: R and ten hex digits.",
    "decision": "Decision ids minted for an unnamed decision. Default {prefix}{hash}: D and ten hex digits.",
    "memory": "Memory ids. Default {prefix}{hash}: M and ten hex digits.",
    "job": "Long-running job ids. Default {prefix}{hash}: J and ten hex digits.",
    "session": "Session ids. Default s{time}-{pid}: s, the UTC time to the second, the process id.",
    "fix_task": "A bug's own fix task. Default fix-{parent}: fix- and the bug id.",
    "fix_task_followup": "The next fix task when a bug's fix task was abandoned. Default fix-{parent}-{seq}: fix-<bug>-2, -3, ...",
    "promotion": "Promotion items. Default promote-{env}-{seq}: promote-staging-1, ...",
    "ci_bug": "Bugs filed for a failing CI check. Default Bci-{slug}-{digest}, a STABLE template: the same failing check maps to the same bug while it is open.",
    "split_child": "Sub-items made by `ddflow split` when none is named. Default {parent}.{seq}: P.T.1, P.T.2, ...",
    "imported_phase": "Phases created by `ddflow import`. Default {user-text}: the id the source file declares, else a slug of its heading.",
    "imported_task": "Tasks created by `ddflow import` that declare no id. Default {phase}.{slug}: the phase id and a slug of the task's text.",
}
for _kind in ID_KINDS:
    _doc("ids", _kind, _KIND_DOCS[_kind] + _RULES)
del _kind
