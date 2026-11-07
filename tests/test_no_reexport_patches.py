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


def _patched_on_package(path: Path, reexports: dict[str, set[str]]) -> list[str]:
    """``path:line: name`` for each monkeypatch rebinding a re-export on the log package
    itself rather than the sibling submodule that actually reads the name.

    pytest's ``monkeypatch.setattr`` takes ``(target, name, value)``, the dotted-string
    short form ``setattr("ddflow.infra.log.APPEND_HOOKS", value)`` (the last ``.part`` is
    the name), and keyword arguments; all three rebind the name on the package and must be
    refused alike."""
    tree = ast.parse(path.read_text(), filename=str(path))
    log_names = _log_package_bindings(tree)
    bad: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "setattr"):
            continue
        pos = list(node.args)
        kw = {k.arg: k.value for k in node.keywords}
        target = pos[0] if len(pos) >= 1 else kw.get("target")
        name = pos[1] if len(pos) >= 2 else kw.get("name")
        value = pos[2] if len(pos) >= 3 else kw.get("value")
        if value is not None:
            # ``setattr(obj, "name", value)`` or ``setattr(target=..., name=..., value=...)``:
            # ``name`` is the attribute being set.
            if not (isinstance(name, ast.Constant) and isinstance(name.value, str)):
                continue
            attr = name.value
            if attr not in reexports or not _is_log_package(target, log_names):
                continue
        else:
            # ``setattr("ddflow.infra.log.<name>", value)``: the dotted string names the
            # target path, its last part the attribute.
            if not (isinstance(target, ast.Constant) and isinstance(target.value, str)):
                continue
            path_str, attr = _dotted_string_name(target.value)
            if attr not in reexports or path_str != "ddflow.infra.log":
                continue
        bad.append(f"{path.name}:{node.lineno}: {attr}")
    return bad


def test_no_test_patches_an_infra_log_reexport_on_the_package():
    reexports = _by_value_reexports()
    offenders = []
    for p in sorted(_TESTS.glob("test_*.py")):
        offenders.extend(_patched_on_package(p, reexports))
    assert not offenders, offenders
