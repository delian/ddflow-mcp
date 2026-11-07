"""No test patches an ``infra.log`` by-value re-export (B-log-reexport-patch-passes-vacuously).

``ddflow.infra.log`` is a split package: ``__init__`` re-exports names BY VALUE from its
sibling submodules (``from ._eventlog import APPEND_HOOKS as APPEND_HOOKS``). A
``monkeypatch.setattr`` on the PACKAGE rebinds only the package's copy of such a name; the
submodule keeps its own binding, so the code the test means to reach still reads the
original and the test passes without the patch ever firing
(``test_shard_high_water.py`` did this and proved nothing). Patch the submodule instead:
``monkeypatch.setattr(L._eventlog, "APPEND_HOOKS", [...])``.
"""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_LOG_INIT = _ROOT / "ddflow" / "infra" / "log" / "__init__.py"
_TESTS = _ROOT / "tests"


def _by_value_reexports() -> dict[str, set[str]]:
    """Map each eagerly re-exported name to the sibling submodules ``__init__`` imports it
    from (``from ._eventlog import NAME``). Submodules importing from ``...core`` (level != 1)
    are not the split's own bindings, so they are left out."""
    tree = ast.parse(_LOG_INIT.read_text(), filename=str(_LOG_INIT))
    out: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level != 1 or not node.module.startswith("_"):
            continue
        for alias in node.names:
            out.setdefault(alias.asname or alias.name, set()).add(node.module)
    return out


def _log_package_bindings(tree: ast.Module) -> set[str]:
    """The local names a test file binds to ``ddflow.infra.log`` itself (``import
    ddflow.infra.log as L`` or ``from ddflow.infra import log as L``)."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "ddflow.infra.log":
                    # bare ``import ddflow.infra.log`` binds ``ddflow``; the package is
                    # then reached as ``ddflow.infra.log`` (an attribute chain), handled by
                    # ``_is_log_package`` -- not a name we can key off here.
                    if alias.asname:
                        names.add(alias.asname)
        elif isinstance(node, ast.ImportFrom) and node.module == "ddflow.infra":
            for alias in node.names:
                if alias.name == "log":
                    names.add(alias.asname or "log")
    return names


def _dotted_path(node: ast.expr) -> str | None:
    """``"ddflow.infra.log"`` for an attribute chain, the bare name for a ``Name``, else
    ``None``."""
    if isinstance(node, ast.Attribute):
        base = _dotted_path(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _is_log_package(target: ast.expr, log_names: set[str]) -> bool:
    """Whether ``target`` is ``ddflow.infra.log`` (spelled out, bound to a local alias, or
    passed as the module-path string pytest imports) rather than one of its submodules or an
    unrelated module."""
    if isinstance(target, ast.Name) and target.id in log_names:
        return True
    if isinstance(target, ast.Constant) and isinstance(target.value, str):
        return target.value == "ddflow.infra.log"
    return _dotted_path(target) == "ddflow.infra.log"


def _dotted_string_name(dotted: str) -> tuple[str, str]:
    """Split a pytest dotted-import string into (target path, attribute name)."""
    parent, _, attr = dotted.rpartition(".")
    return parent, attr


def _patched_reexport(
    node: ast.Call, log_names: set[str], reexports: dict[str, set[str]]
) -> str | None:
    """The re-export name a ``monkeypatch.setattr`` rebinds on the log PACKAGE, or ``None``
    when the call is not such a patch. The name is the string it sets (``"APPEND_HOOKS"``)
    or ``"<dynamic>"`` for a variable attribute the guard cannot resolve statically."""
    pos = list(node.args)
    kw = {k.arg: k.value for k in node.keywords}
    target = pos[0] if len(pos) >= 1 else kw.get("target")
    name = pos[1] if len(pos) >= 2 else kw.get("name")
    value = pos[2] if len(pos) >= 3 else kw.get("value")
    if value is not None:
        # ``setattr(obj, "name", value)`` / ``setattr(target=…, name=…, value=…)``: ``name``
        # is the attribute being set.
        if isinstance(name, ast.Constant) and isinstance(name.value, str):
            return name.value if name.value in reexports and _is_log_package(target, log_names) else None
        # A variable attribute name on the package re-spells any re-export and cannot be
        # resolved statically: flag it so the author spells the submodule patch out.
        return "<dynamic>" if _is_log_package(target, log_names) else None
    # Two-arg form. ``setattr("ddflow.infra.log.<name>", value)`` gives the target path as a
    # dotted string; ``setattr(L, "APPEND_HOOKS")`` deletes the package attribute.
    if isinstance(target, ast.Constant) and isinstance(target.value, str):
        path_str, attr = _dotted_string_name(target.value)
        return attr if attr in reexports and path_str == "ddflow.infra.log" else None
    if isinstance(name, ast.Constant) and isinstance(name.value, str):
        return name.value if name.value in reexports and _is_log_package(target, log_names) else None
    return None


def _offenders(src: str, reexports: dict[str, set[str]]) -> list[tuple[int, str]]:
    """``(line, name)`` for each monkeypatch in ``src`` rebinding a re-export on the log
    package itself rather than the sibling submodule that actually reads the name."""
    tree = ast.parse(src)
    log_names = _log_package_bindings(tree)
    bad = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "setattr"):
            continue
        attr = _patched_reexport(node, log_names, reexports)
        if attr is not None:
            bad.append((node.lineno, attr))
    return bad


def _patched_on_package(path: Path, reexports: dict[str, set[str]]) -> list[str]:
    """``path:line: name`` for each offender in one test file."""
    return [f"{path.name}:{line}: {name}" for line, name in _offenders(path.read_text(), reexports)]


def test_no_test_patches_an_infra_log_reexport_on_the_package():
    reexports = _by_value_reexports()
    offenders = []
    for p in sorted(_TESTS.rglob("test_*.py")):
        offenders.extend(_patched_on_package(p, reexports))
    assert not offenders, offenders


_PACKAGE_PATCH_FORMS = [
    'from ddflow.infra import log as L\nmonkeypatch.setattr(L, "APPEND_HOOKS", [])',
    'import ddflow.infra.log as L\nmonkeypatch.setattr(L, "APPEND_HOOKS", [])',
    'import ddflow.infra.log\nmonkeypatch.setattr(ddflow.infra.log, "APPEND_HOOKS", [])',
    'monkeypatch.setattr("ddflow.infra.log.APPEND_HOOKS", [])',
    'monkeypatch.setattr("ddflow.infra.log", "APPEND_HOOKS", [])',
    'monkeypatch.setattr(target="ddflow.infra.log", name="APPEND_HOOKS", value=[])',
    # deletion form: omitting ``value`` deletes the package attribute only
    'import ddflow.infra.log as L\nmonkeypatch.setattr(L, "APPEND_HOOKS")',
]


def test_guard_flags_every_package_patch_form():
    """Each way pytest lets a test rebind a re-export on the PACKAGE is refused, so the
    guard cannot be bypassed by re-spelling the same call."""
    reexports = _by_value_reexports()
    for src in _PACKAGE_PATCH_FORMS:
        names = [name for _, name in _offenders(src, reexports)]
        assert names == ["APPEND_HOOKS"], (src, names)


def test_guard_flags_a_dynamic_attribute_name_on_the_package():
    """An attribute spelled as a local variable on the package re-spells any re-export and
    the guard cannot resolve it statically, so it is refused rather than silently missed."""
    reexports = _by_value_reexports()
    src = (
        'from ddflow.infra import log as L\n'
        'attr = "APPEND_HOOKS"\n'
        'monkeypatch.setattr(L, attr, [])'
    )
    names = [name for _, name in _offenders(src, reexports)]
    assert names == ["<dynamic>"], names


def test_guard_allows_patching_the_submodule_that_reads_the_name():
    """The submodule is where the name is read: patching it fires and is not refused."""
    reexports = _by_value_reexports()
    src = 'from ddflow.infra import log as L\nmonkeypatch.setattr(L._eventlog, "APPEND_HOOKS", [])'
    assert _offenders(src, reexports) == []
