"""The adapter model, pinned: `ddflow/core/ports.py` is the one place a port is declared,
and declaring a port is the nothing-else it does.

The pattern itself — where ports and adapters live, how a family names its default and its
fake, and when a family registers its several adapters in `core.registry.Registry[T]` — is
written in `docs/ARCHITECTURE.md` ("Adapter model"). This file is the machine check that
the code half of that one document does not drift from it, in the same way a layering rule
written only in prose decayed into a dependency graph.

A port is a declaration, not an implementation: if `ddflow/core/ports.py` grows code, it is
a second implementation of the seam it declares, which is exactly the bug the guards retire.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ddflow.core import ports as P

PKG = Path(__file__).resolve().parents[1] / "ddflow"
PORTS_PATH = PKG / "core" / "ports.py"

#: The families that have a port. `forge` is the multi-adapter family (GitHub, GitLab,
#: registered in a `Registry[Forge]`); the other five have one default implementation each.
FAMILIES = {"Proc", "Git", "Fsio", "Toml", "Digest", "Forge"}


def _ports() -> dict[str, type]:
    """Every public `Protocol` `ddflow/core/ports.py` declares, by its class name."""
    declared = P.Protocol
    return {
        name: value
        for name, value in vars(P).items()
        if isinstance(value, type) and value is not declared and issubclass(value, declared)
    }


def test_the_port_families_are_exactly_the_declared_seams():
    assert set(_ports()) == FAMILIES, (
        "a port appeared or vanished without touching this contract test: a new seam is a "
        "declared change of model, not a surprise of the next refactor."
    )


def _opaque(node: ast.AST) -> ast.AST | None:
    """A statement that runs code. A docstring and an ellipsis (`...`) are declarations;
    `pass` and a bare annotation (`name: str`) are ones too; everything else — a `return`,
    an assignment, a call — is a body, and a port with a body is a second implementation
    wearing the port's name."""
    if isinstance(node, ast.Pass):
        return None
    if isinstance(node, ast.AnnAssign) and node.value is None:
        return None  # a member declaration such as `name: str`, not code
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
        value = node.value.value
        if isinstance(value, str) or value is Ellipsis:
            return None
    return node


def _runs_code(expr: ast.expr) -> bool:
    """An expression in a decorator or base position that executes at class-definition
    time. ``@runtime_checkable`` marks a port without running the port code, and a bare
    name is a reference; anything else (a call, an attribute) is code."""
    return not (isinstance(expr, ast.Name) and expr.id == "runtime_checkable")


def test_ports_are_declarations_only():
    """`ddflow/core/ports.py` holds a docstring, imports and `Protocol` classes, and every
    method in them is a docstring or `...` — nothing runs, not in a body, a decorator or a
    base."""
    tree = ast.parse(PORTS_PATH.read_text("utf-8"), str(PORTS_PATH))
    statements: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom | ast.Import):
            continue
        if isinstance(node, ast.ClassDef):
            for decorator in node.decorator_list:
                if _runs_code(decorator):
                    statements.append(f"{node.name}: a decorator runs code at definition")
            for base in node.bases:
                if isinstance(base, ast.Name) and base.id == "Protocol":
                    continue
                statements.append(f"{node.name}: a base {ast.dump(base)} runs code")
            for member in node.body:
                if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
                    for decorator in member.decorator_list:
                        if _runs_code(decorator):
                            statements.append(
                                f"{node.name}.{member.name}: a method decorator runs code"
                            )
                    statements += [
                        f"{node.name}.{member.name}: a method body runs code"
                        for body in member.body
                        if _opaque(body) is not None
                    ]
                elif _opaque(member) is not None:
                    statements.append(ast.dump(member))
            continue
        if _opaque(node) is None:
            continue  # the module docstring
        statements.append(ast.dump(node))
    assert not statements, (
        f"ports.py runs code: {statements}. A port declares; the default adapter in "
        "ddflow/adapters/ implements it."
    )


def test_ports_import_no_ddflow_module_and_no_io():
    """A port is pure: `core` takes no disk, network or subprocess, and `ddflow.adapters`
    (which the ports describe) is below `core`, so the port's types must come from the
    standard library alone. Checked for a plain `import subprocess` and a relative import
    into `ddflow` too, not only for `from ddflow...`."""
    io_modules = {"subprocess", "socket", "urllib", "http", "sqlite3", "shutil", "tempfile"}
    tree = ast.parse(PORTS_PATH.read_text("utf-8"), str(PORTS_PATH))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if alias.name.startswith("ddflow") or top in io_modules:
                    offenders.append(top)
        elif isinstance(node, ast.ImportFrom):
            top = (node.module or "").split(".")[0]
            if node.level:
                offenders.append("." * node.level + (node.module or ""))
            elif top == "ddflow" or top in io_modules:
                offenders.append(top)
    assert not offenders, f"ports.py must import the standard library alone, not {offenders}"


def test_ports_module_is_under_twenty_kib():
    assert PORTS_PATH.stat().st_size < 20 * 1024, (
        "r-small-files: ddflow/core/ports.py grew past 20 KB; the default impls live in "
        "ddflow/adapters/, not here."
    )


def test_a_single_adapter_family_has_no_name_and_a_multi_adapter_family_has_one():
    """`name` is the registry's key, so only a family with SEVERAL adapters carries it:
    `Forge` is the worked example (`Registry[Forge]`, keyed by name); `proc`, `git`, `fsio`,
    `tomlcfg` and `digest` each have one default implementation, and a second one is a bug,
    not a registry entry."""
    ports = _ports()
    multi = ports["Forge"]
    assert "name" in multi.__annotations__, "Forge must declare `name` (its registry key)"
    single = set(ports) - {"Forge"}
    for family in sorted(single):
        assert "name" not in ports[family].__annotations__, (
            f"{family} is a single-adapter family; a `name` there invites a registry with "
            "one entry."
        )


class _FakeProbe:
    """A nominal stand-in for a `Proc`, proving the port is duck-typed, not nominal."""

    def run(self, *args, **kwargs):
        return ("run", args, kwargs)

    def capture(self, argv, *, timeout=None, **kwargs):
        return ("capture", argv, timeout)

    def spawn_shell(self, line, **kwargs):
        return ("spawn_shell", line)


def test_a_nominal_fake_satisfies_a_runtime_checkable_port():
    """`isinstance` accepts a stand-in with the port's methods — the property that lets a
    fake in `tests/fakes/` be dropped in where the adapter module is imported."""
    assert isinstance(_FakeProbe(), P.Proc)


def test_the_fake_stays_in_tests_and_is_the_only_second_implementation():
    """One port, one default and one fake: the fake is the ONE permitted stand-in, and it
    is a tests helper (`tests/fakes/`), never shipped code in `ddflow/`."""
    assert not (PKG / "fakes").exists(), (
        "fakes belong in tests/fakes/; a ddflow/fakes/ package would ship test doubles."
    )


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_every_port_documents_its_home(family: str):
    """A port names where its default adapter lives, so the "where" half of the model is
    next to the code half, not only in the architecture document."""
    doc = _ports()[family].__doc__ or ""
    assert "ddflow.adapters." in doc, (
        f"{family} does not name its home in ddflow/adapters/: the port is the seam, and "
        "the doc says where the default lands."
    )
