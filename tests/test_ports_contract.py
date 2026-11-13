"""The adapter model, pinned, generically: it iterates the families it DISCOVERS, so a new port
adds no line here (ADP-modular-registry).

A family is `ddflow/adapters/<family>.py` (its `@port` Protocol and default adapter),
`ddflow/adapters/fakes/<family>.py` (`@fake class Fake<Port>`), `tests/test_adapter_<family>.py`
(a `CONFORMANCE` table) and `docs/ddflow/adapters/<family>.md`; `ddflow.adapters.ports` finds
the ports and `ddflow.adapters.fakes` the fakes by scanning those packages in sorted module
order. The pattern is in `docs/ddflow/adapters/README.md`. This file is the machine check that
the code half does not drift from it, the way a layering rule written only in prose decayed
into a dependency graph.

A port is a declaration, not an implementation: a `@port` class with a body is a second
implementation of the seam it declares, which is exactly the bug the guards retire.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

import ddflow.adapters as A
from ddflow.adapters import fakes as F
from ddflow.adapters import ports as P

PKG = Path(__file__).resolve().parents[1] / "ddflow"
PORTS_PATH = PKG / "adapters" / "ports.py"
BASELINES = PKG.parent / "tests" / "guard_baselines" / "ports_without_fake"


def _ports() -> dict[str, type]:
    """Every `@port` Protocol under `ddflow/adapters`, by class name (discovered)."""
    return P.discover(A)


def _family(cls: type) -> str:
    return cls.__module__.rsplit(".", 1)[1]


def test_the_ports_are_found_by_scanning_not_listed():
    found = _ports()
    assert found, "discovery found no port"
    for name, cls in found.items():
        assert isinstance(cls, type) and getattr(cls, "_is_protocol", False), (
            f"{name} is no Protocol"
        )
        assert cls.__module__ == f"ddflow.adapters.{_family(cls)}"
    assert P.module_names(A) == sorted(P.module_names(A)), "module scan must be sorted"
    assert list(found) == [
        n for m in P.module_names(A) for n in found if found[n].__module__.endswith(f".{m}")
    ], "ports must come in sorted module order"


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
    time. ``@port`` and ``@runtime_checkable`` mark a port without running the port code, and a bare
    name is a reference; anything else (a call, an attribute) is code."""
    return not (isinstance(expr, ast.Name) and expr.id in ("runtime_checkable", "port"))


def _literal(expr: ast.expr | None) -> bool:
    """A literal, which reads: a constant, `None`/`True`/`False`, a container built of
    literals, or a signed `int`/`float` constant under `+`/`-` (and `~` on an `int`). A call
    (`exec(...)`, `frozenset(...)`) runs code, so it is not a literal, however cheap the code."""
    if expr is None or isinstance(expr, ast.Constant):
        return True
    if isinstance(expr, ast.Name) and (expr.id in ("None", "True", "False") or expr.id.isupper()):
        return True  # an ALL_CAPS module constant is a reference, not code
    if isinstance(expr, ast.Tuple | ast.List | ast.Set):
        return all(_literal(e) for e in expr.elts)
    if isinstance(expr, ast.Dict):
        return all(
            _literal(k) and _literal(v) for k, v in zip(expr.keys, expr.values, strict=False)
        )
    if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.USub | ast.UAdd):
        return isinstance(expr.operand, ast.Constant) and isinstance(
            expr.operand.value, int | float
        )
    if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Invert):
        return isinstance(expr.operand, ast.Constant) and isinstance(expr.operand.value, int)
    return False


def _class_code(node: ast.ClassDef) -> list[str]:
    """What in one `Protocol` class runs code: a decorator, a base, a method body or a
    method decorator. A port is a declaration, so any of them is a second implementation."""
    out = [
        f"{node.name}: a decorator runs code at definition"
        for d in node.decorator_list
        if _runs_code(d)
    ]
    out += [
        f"{node.name}: a base {ast.dump(b)} runs code"
        for b in node.bases
        if not (isinstance(b, ast.Name) and b.id == "Protocol")
    ]
    for member in node.body:
        if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
            out += [
                f"{node.name}.{member.name}: a method decorator runs code"
                for d in member.decorator_list
                if _runs_code(d)
            ]
            out += [
                f"{node.name}.{member.name}: a method body runs code"
                for body in member.body
                if _opaque(body) is not None
            ]
            out += [
                f"{node.name}.{member.name}: an argument default runs code"
                for default in (*member.args.defaults, *member.args.kw_defaults)
                if default is not None and not _literal(default)
            ]
        elif _opaque(member) is not None:
            out.append(ast.dump(member))
    return out


def _port_nodes(path: Path) -> list[ast.ClassDef]:
    """The `@port` class definitions in one module's source."""
    tree = ast.parse(path.read_text("utf-8"), str(path))
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef)
        and any(isinstance(d, ast.Name) and d.id == "port" for d in n.decorator_list)
    ]


def _module_path(cls: type) -> Path:
    return PKG.parent / (cls.__module__.replace(".", "/") + ".py")


@pytest.mark.parametrize("name", sorted(_ports()))
def test_a_port_is_a_declaration_only(name: str):
    """A `@port` class holds a docstring and member declarations, and every method in it is a
    docstring or `...` -- nothing runs, not in a body, a decorator or a base."""
    nodes = [n for n in _port_nodes(_module_path(_ports()[name])) if n.name == name]
    assert len(nodes) == 1, f"{name}: one definition, not {len(nodes)} (r-reuse-core)"
    statements = _class_code(nodes[0])
    assert not statements, (
        f"{name} runs code: {statements}. A port declares; the default adapter implements it."
    )


def test_literal_classifies_signed_and_container_defaults():
    """The literal classifier itself: a signed numeric constant reads, a unary on a string
    or a `float` under `~` runs. A guard that drifted here would silently widen what the
    declarations-only ratchet accepts, so its corner cases are pinned, not observed."""
    from ast import Constant, Dict, Invert, List, Name, Tuple, USub

    assert _literal(ast.Constant(-1))
    assert _literal(ast.Constant("sha256"))
    assert _literal(Name(id="None"))
    assert _literal(Tuple(elts=[Constant("a"), Constant("b")]))
    assert _literal(List(elts=[Constant(1)]))
    assert _literal(Dict(keys=[Constant("a")], values=[Constant(1)]))
    assert _literal(ast.parse("-1", mode="eval").body)
    assert _literal(ast.UnaryOp(op=USub(), operand=Constant(""))) is False
    assert _literal(ast.UnaryOp(op=Invert(), operand=Constant(1.0))) is False
    assert _literal(ast.parse("exec('print(1)')").body) is False


def test_the_ports_base_imports_the_standard_library_alone():
    """`ports.py` is the base every family imports: it names no ddflow module (not even by a
    relative import) and no I/O module, so a family cannot be pulled in by it."""
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


@pytest.mark.parametrize("path", sorted((PKG / "adapters").rglob("*.py")), ids=lambda p: p.name)
def test_every_adapter_module_is_under_twenty_kib(path: Path):
    assert path.stat().st_size < 20 * 1024, (
        f"r-small-files: {path.relative_to(PKG.parent)} grew past 20 KB; split the family."
    )


def test_a_single_adapter_family_has_no_name_and_a_multi_adapter_family_has_one():
    """`name` is the registry's key, so only a family with SEVERAL adapters carries it, as a
    declared member of its port. `name` is data, and a `@runtime_checkable` port refuses a
    data member, so the multi-adapter ports ARE the ports that are not `@runtime_checkable`
    -- discovered, not listed: `Forge` is the worked example, not the only one."""
    ports = _ports()
    for family, cls in sorted(ports.items()):
        multi = not getattr(cls, "_is_runtime_protocol", False)
        if multi:
            assert "name" in cls.__annotations__, (
                f"{family}: a multi-adapter family (its port is not `@runtime_checkable`) "
                "must declare `name` as its registry key."
            )
        else:
            assert "name" not in cls.__annotations__, (
                f"{family} is a single-adapter family; a `name` there invites a registry "
                "with one entry."
            )


#: The ceiling the baseline may never exceed: the ports still without a fake when the ratchet
#: began. A SHRINK-ONLY ratchet (D-unify 4): a fake lands with the task that migrates its
#: home and its file in `tests/guard_baselines/ports_without_fake/` is deleted in the same
#: change; a file for any other port fails here, so a new port cannot be excused.
_CEILING = frozenset({"Proc", "Git", "Fsio", "Toml", "Forge"})


def _without_fake() -> set[str]:
    return {p.stem for p in BASELINES.glob("*.txt")}


def test_every_port_has_a_registered_fake_or_is_in_the_shrinking_baseline():
    """Fakes ship in `ddflow.adapters.fakes` (D-adapters-answers-2) and register under the
    port's name; no port may lack one except those with a baseline file, which only shrinks."""
    baseline = _without_fake()
    assert baseline <= _CEILING, (
        f"{sorted(baseline - _CEILING)}: the baseline may only shrink; a new port needs a fake"
    )
    have, ports = set(F.FAKES), set(_ports())
    assert baseline <= ports, f"a baseline file for no port: {sorted(baseline - ports)}; delete it"
    missing = ports - have
    assert missing <= baseline, (
        f"a port without a fake: {sorted(missing - baseline)}. Add ddflow/adapters/fakes/"
        "<family>.py with a `@fake` class; do not add a baseline file."
    )
    assert not (baseline & have), (
        f"{sorted(baseline & have)} now have a fake: delete their files in "
        "tests/guard_baselines/ports_without_fake/ so the ratchet shrinks."
    )
    assert have <= ports, f"a fake for no port: {sorted(have - ports)}"


@pytest.mark.parametrize("name", sorted(F.FAKES))
def test_a_registered_fake_satisfies_its_runtime_checkable_port(name: str):
    port = _ports()[name]
    if not getattr(port, "_is_runtime_protocol", False):
        # A multi-adapter port (it declares `name`) is not @runtime_checkable, so isinstance
        # and issubclass of it raise TypeError; its fake is a nominal subclass instead.
        assert port in F.FAKES[name].__mro__, f"Fake{name} is not a nominal {name} subclass"
        return
    assert isinstance(F.FAKES[name](), port), f"Fake{name} does not satisfy {name}"
    assert issubclass(F.FAKES[name], port), "the `@port` marker must not break issubclass"


def test_the_fakes_are_shipped_in_the_adapters_package():
    assert (PKG / "adapters" / "fakes" / "__init__.py").is_file()
    assert not (PKG / "fakes").exists(), "fakes live in ddflow/adapters/fakes, not ddflow/fakes"


def test_fakes_register_in_sorted_module_order():
    """No hash or filesystem order decides which fake wins: sorted module names do."""
    found = F.collect(F)
    mods = P.module_names(F, frozenset())
    assert mods == sorted(mods), "the fake module scan must be sorted"
    expect = [n for m in mods for n, c in found.items() if c.__module__ == f"{F.__name__}.{m}"]
    assert list(found) == expect and all(F.FAKES[n] is c for n, c in found.items())
    assert sorted(F.FAKES) == sorted(found)
    assert found, "fake discovery found nothing"
    assert {c.__module__.rsplit(".", 1)[1] for c in found.values()} == {
        m for m in mods if m != "__init__"
    }, "every module of the fakes package must hold a fake"
    assert all(getattr(F, c.__name__) is c for c in found.values()), "Fake<Port> stays importable"


# -- conformance: one set of cases, run against the adapter and its fake ----------------


def _conformance(name: str) -> dict:
    """The family's `CONFORMANCE[name]` from `tests/test_adapter_<family>.py`."""
    family = _family(_ports()[name])
    module = importlib.import_module(f"tests.test_adapter_{family}")
    return module.CONFORMANCE[name]


def test_every_fake_has_a_conformance_case_and_a_real_adapter():
    for name in sorted(F.FAKES):
        entry = _conformance(name)
        assert callable(entry["real"]) and callable(entry["cases"]), (
            f"{name}: CONFORMANCE needs `real` (monkeypatch -> adapter), `cases` (adapter) and optionally `fake` (registered class -> instance)"
        )


@pytest.mark.parametrize("name", sorted(F.FAKES))
def test_adapter_and_fake_pass_the_same_cases(name: str, monkeypatch):
    entry = _conformance(name)
    entry["cases"](entry["real"](monkeypatch))
    # always the REGISTERED fake; a family's `fake` factory only wires it up (cls -> instance)
    registered = F.FAKES[name]  # read before the factory runs: it may not swap the class
    made = entry.get("fake", lambda cls: cls())(registered)
    assert type(made) is registered, f"the `fake` factory must build the registered Fake{name}"
    entry["cases"](made)


def test_the_registry_has_one_implementation_and_core_re_exports_it():
    """r-reuse-core: `adapters` is the bottom layer and cannot import `core`, so the ONE
    `Registry[T]` lives in `ddflow.adapters.registry`; `ddflow.core.registry` is a re-export
    of the same objects, not a second copy."""
    from ddflow.adapters import registry as A
    from ddflow.core import registry as C

    assert C.Registry is A.Registry and C.DuplicateName is A.DuplicateName
    assert C.UnknownName is A.UnknownName and C.Lazy is A.Lazy
    assert (PKG / "core" / "registry.py").stat().st_size < 2048, "core/registry.py is a shim"
    assert F.FAKES.what == "fake" and isinstance(F.FAKES, A.Registry)


def test_the_old_core_ports_path_is_gone():
    assert not (PKG / "core" / "ports.py").exists(), "ports live in ddflow/adapters/ports.py"


def test_the_layer_contract_puts_adapters_at_the_bottom():
    text = (PKG.parent / ".importlinter").read_text("utf-8")
    block = text.split("[importlinter:contract:layers]", 1)[1].split("[importlinter:", 1)[0]
    layers = [
        ln.strip()
        for ln in block.split("layers =", 1)[1].splitlines()
        if ln.strip().startswith("ddflow.")
    ]
    assert layers[-2:] == ["ddflow.core", "ddflow.adapters"], layers


@pytest.mark.parametrize("name", sorted(_ports()))
def test_every_port_documents_its_home(name: str):
    """A port names where its default adapter lives, so the "where" half of the model is
    next to the code half, not only in the architecture document."""
    doc = _ports()[name].__doc__ or ""
    assert "ddflow.adapters." in doc, (
        f"{name} does not name its home in ddflow/adapters/: the port is the seam, and "
        "the doc says where the default lands."
    )
