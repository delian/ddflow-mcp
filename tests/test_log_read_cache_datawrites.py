"""The invariant the cache rests on: nothing mutates an event's `data` dict in place.

The cache hands out the SAME parsed Event objects to every reader, so one in-place write
would corrupt every later read. These tests hold the AST detector and its self-checks.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ast

import pytest
from log_cache_support import _cold_cache, _no_version_stamp  # noqa: F401

# -- the invariant the cache rests on ------------------------------------------------


#: Receiver names whose `.data` is NOT an event's. `Outcome`, in every case.
_ALLOWED_DATA_RECEIVERS = {"out"}
#: dict methods that mutate in place. `setdefault` is here because it WRITES on a miss,
#: which reads like a lookup at the call site.
_DICT_MUTATORS = frozenset({"update", "pop", "popitem", "setdefault", "clear"})


def _data_receiver(node: ast.AST) -> str | None:
    """The name whose `.data` this expression touches, or None.

    Returns the unparsed receiver, so `self.ev.data` and `ev.data` are both reported —
    an attribute receiver is just as capable of holding a cached event.
    """
    if isinstance(node, ast.Attribute) and node.attr == "data":
        base = node.value
        return base.id if isinstance(base, ast.Name) else ast.unparse(base)
    return None


def _write_targets(node: ast.AST) -> list[ast.expr]:
    """What this statement writes to: `x = `, `x += `, `x: T = ` and `del x` alike.

    `AnnAssign` is here because `ev.data['k']: int = 1` is a write that the first version
    of this walked straight past, and `AugAssign` because `ev.data |= {...}` is PEP 584's
    in-place dict merge — the standard way to do the thing this ratchet forbids. Both
    found by the cross-family critic.
    """
    if isinstance(node, ast.Assign):
        return list(node.targets)
    if isinstance(node, ast.AugAssign | ast.AnnAssign):
        return [node.target]
    if isinstance(node, ast.Delete):
        return list(node.targets)
    return []


def _scope_body(scope: ast.AST) -> list[ast.AST]:
    """Every node lexically inside `scope`, NOT descending into a nested function or class.

    `ast.walk` descends into everything, which made an alias bound in one function visible
    in every other: `def a(ev): d = ev.data` registered `d` at module level, and an
    unrelated `d = {}; d['k'] = 1` in a sibling function was then reported as mutating an
    event. It over-fired rather than under-fired, so it would have shown up as a spurious
    red build rather than a miss — but a ratchet that cries wolf gets switched off, and
    the docstring said "in the same scope" while the code did not.
    """
    out: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        out.append(node)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
            continue  # its own scope; visited separately
        stack.extend(ast.iter_child_nodes(node))
    return out


def _direct_data_writes(tree: ast.AST, allowed: set[str]) -> list[str]:
    """Writes straight through the attribute: `ev.data[k] = v`, `ev.data |= {...}`,
    `del ev.data[k]`, `ev.data.update(...)`."""
    out = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        for t in _write_targets(node):
            # `ev.data[k] = v` -- the subscript's receiver is the `.data` attribute.
            if isinstance(t, ast.Subscript):
                r = _data_receiver(t.value)
                if r and r not in allowed:
                    out.append(f"{line}: {r}.data[...] written")
            # `ev.data |= {...}` / `ev.data = ...` -- the TARGET is the attribute itself.
            else:
                r = _data_receiver(t)
                if r and r not in allowed:
                    out.append(f"{line}: {r}.data written whole")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _DICT_MUTATORS
        ):
            r = _data_receiver(node.func.value)
            if r and r not in allowed:
                out.append(f"{line}: {r}.data.{node.func.attr}()")
    return out


def _aliased_data_writes(tree: ast.AST, allowed: set[str]) -> list[str]:
    """`d = ev.data` and then `d[k] = v` in the SAME scope.

    Aliasing to READ is everywhere and fine — `d = ev.data; d.get(...)`, 14 sites — so the
    alias alone is not the offence. Mutating through it is, and it is the route that
    escapes a direct-write scan entirely. Attribute aliases (`self.d = ev.data`) count too.
    """
    out = []
    scopes: list[ast.AST] = [tree]
    scopes += [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda)
    ]
    for scope in scopes:
        nodes = _scope_body(scope)
        aliases: dict[str, int] = {}
        for n in nodes:
            if not isinstance(n, ast.Assign):
                continue
            r = _data_receiver(n.value)
            if not r or r in allowed:
                continue
            for t in n.targets:
                if isinstance(t, ast.Name | ast.Attribute):
                    aliases[ast.unparse(t)] = n.lineno
        if not aliases:
            continue
        for n in nodes:
            line = getattr(n, "lineno", 0)
            for t in _write_targets(n):
                if isinstance(t, ast.Subscript) and ast.unparse(t.value) in aliases:
                    name = ast.unparse(t.value)
                    out.append(f"{line}: {name}[...] written; aliases event data")
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr in _DICT_MUTATORS
                and ast.unparse(n.func.value) in aliases
            ):
                name = ast.unparse(n.func.value)
                out.append(f"{line}: {name}.{n.func.attr}(); aliases event data")
    return out


def data_writes(src: str, allowed: set[str] | None = None) -> list[str]:
    """Every route by which `src` could mutate an event's `data` dict.

    "Every" is a claim the parametrised test below keeps honest: three of these routes
    were added after a reviewer showed the previous version walked past them.
    """
    allowed = _ALLOWED_DATA_RECEIVERS if allowed is None else allowed
    tree = ast.parse(src)
    return _direct_data_writes(tree, allowed) + _aliased_data_writes(tree, allowed)


@pytest.mark.parametrize(
    "planted",
    [
        "ev.data['k'] = 1",
        "ev.data['k'] += 1",
        "del ev.data['k']",
        "ev.data.update({'k': 1})",
        "ev.data.setdefault('k', 1)",
        "ev.data.pop('k')",
        "ev.data.clear()",
        "def f(ev):\n    d = ev.data\n    d['k'] = 1\n",
        "def f(ev):\n    d = ev.data\n    d.update({'k': 1})\n",
        "def f(ev):\n    d = ev.data\n    del d['k']\n",
        # The three the cross-family critic found the first version walking past.
        "ev.data |= {'k': 1}",  # PEP 584 in-place dict merge
        "ev.data['k']: int = 1",  # annotated subscript assignment
        "def f(self, ev):\n    self.d = ev.data\n    self.d['k'] = 1\n",  # attribute alias
        "def f(self, ev):\n    self.d = ev.data\n    self.d.update({'k': 1})\n",
        # An attribute RECEIVER, not an attribute alias: the event itself is held on an
        # object. `_data_receiver` must unparse it rather than give up on non-Name bases.
        "self.ev.data['k'] = 1",
        "self.ev.data.update({'k': 1})",
        "rec(item).ev.data['k'] = 1",
    ],
)
def test_the_data_write_detector_catches_every_route(planted):
    """The detector is fed planted input, so it can be SHOWN to work.

    A scan whose only input is the live tree passes identically when it has silently
    stopped looking — the failure mode `test_ratchet_no_dead_knobs` names, and the reason
    the first version of this ratchet missed three of these ten routes.
    """
    assert data_writes(planted), f"the detector walked past: {planted!r}"


def test_reading_an_events_data_is_not_flagged():
    """The other half: a ratchet that fires on `d.get(...)` would be turned off."""
    assert not data_writes("def f(ev):\n    d = ev.data\n    return d.get('k'), ev.data['j']\n")
    assert not data_writes("out.data['text'] = 'allowlisted: this is an Outcome'")


def test_an_alias_in_one_function_does_not_leak_into_another(repo):
    """The over-fire the cross-family critic found: `ast.walk` descends into every nested
    body, so an alias bound in one function registered at module level and an unrelated
    local of the same name in a sibling was reported as mutating an event.

    It fails in the SAFE direction — a spurious red rather than a miss — but a ratchet
    that cries wolf gets switched off, and `_aliased_data_writes` said "in the same scope"
    while doing nothing of the kind.
    """
    leaky = "def a(ev):\n    d = ev.data\n    return d\ndef b():\n    d = {}\n    d['k'] = 1\n"
    assert not data_writes(leaky), (
        "an unrelated local that merely shares a name with an event-data alias was flagged"
    )
    # ...while a mutation in the scope that DOES hold the alias is still caught.
    real = "def a(ev):\n    d = ev.data\n    d['k'] = 1\n"
    assert data_writes(real), "narrowing the scope broke the thing it is here to catch"


def test_nothing_in_the_package_mutates_an_events_data_dict():
    """`Event` is a frozen dataclass whose `data` dict is NOT frozen.

    Before the cache, every `read_all()` built fresh `Event` objects, so mutating one was
    invisible. They are now shared across every read in the process, and a single
    `ev.data[k] = v` anywhere would poison every later caller — a corruption that surfaces
    in an unrelated command, long after the line that caused it.

    A ratchet, not a style rule: `_ALLOWED_DATA_RECEIVERS` may only shrink.
    """
    import pathlib as _p

    pkg = _p.Path(__file__).resolve().parents[1] / "ddflow"
    offenders = []
    for f in sorted(pkg.rglob("*.py")):
        if "__pycache__" in str(f):
            continue
        offenders += [
            f"{f.relative_to(pkg.parent)}:{hit}" for hit in data_writes(f.read_text("utf-8"))
        ]
    assert not offenders, (
        "a shared, cached Event's data dict is being mutated (or a new Outcome receiver "
        f"needs allowlisting with its reason): {offenders}"
    )
