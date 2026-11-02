"""The adapter model, pinned: `ddflow/adapters/ports.py` is the one place a port is declared,
and declaring a port is the nothing-else it does.

The pattern itself — where ports and adapters live, how a family names its default and its
fake, and when a family registers its several adapters in `ddflow.adapters.registry.Registry[T]` — is
written in `docs/ARCHITECTURE.md` ("Adapter model"). This file is the machine check that
the code half of that one document does not drift from it, in the same way a layering rule
written only in prose decayed into a dependency graph.

A port is a declaration, not an implementation: if `ddflow/adapters/ports.py` grows code, it is
a second implementation of the seam it declares, which is exactly the bug the guards retire.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ddflow.adapters import fakes as F
from ddflow.adapters import ports as P

PKG = Path(__file__).resolve().parents[1] / "ddflow"
PORTS_PATH = PKG / "adapters" / "ports.py"

#: The families that have a port. `forge` is the multi-adapter family (GitHub, GitLab,
#: registered in a `Registry[Forge]`); the other five have one default implementation each.
FAMILIES = {"Proc", "Git", "Fsio", "Toml", "Digest", "Forge", "HttpClient"}


def _ports() -> dict[str, type]:
    """Every public `Protocol` `ddflow/adapters/ports.py` declares, by its class name."""
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


def _literal(expr: ast.expr | None) -> bool:
    """A literal, which reads: a constant, `None`/`True`/`False`, a container built of
    literals, or a signed `int`/`float` constant under `+`/`-` (and `~` on an `int`). A call
    (`exec(...)`, `frozenset(...)`) runs code, so it is not a literal, however cheap the code."""
    if expr is None or isinstance(expr, ast.Constant):
        return True
    if isinstance(expr, ast.Name) and expr.id in ("None", "True", "False"):
        return True
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


def test_ports_are_declarations_only():
    """`ddflow/adapters/ports.py` holds a docstring, imports and `Protocol` classes, and every
    method in them is a docstring or `...` — nothing runs, not in a body, a decorator or a
    base."""
    tree = ast.parse(PORTS_PATH.read_text("utf-8"), str(PORTS_PATH))
    statements: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom | ast.Import):
            continue
        if isinstance(node, ast.ClassDef):
            statements += _class_code(node)
            continue
        if _opaque(node) is None:
            continue  # the module docstring
        statements.append(ast.dump(node))
    assert not statements, (
        f"ports.py runs code: {statements}. A port declares; the default adapter in "
        "ddflow/adapters/ implements it."
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
        "r-small-files: ddflow/adapters/ports.py grew past 20 KB; the default impls live in "
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


#: Ports that still have NO fake. A SHRINK-ONLY ratchet (D-unify 4): a fake lands with the
#: task that migrates its home (ADP-migrate-homes and the family tasks) and the name leaves
#: this set in the same change; adding a name here, to excuse a new port, fails the test.
PORTS_WITHOUT_FAKE = {"Proc", "Git", "Fsio", "Toml", "Forge"}
#: The ceiling the baseline may never exceed: the ports declared when the ratchet began.
_CEILING = {"Proc", "Git", "Fsio", "Toml", "Forge"}


def test_every_port_has_a_registered_fake_or_is_in_the_shrinking_baseline():
    """Fakes ship in `ddflow.adapters.fakes` (D-adapters-answers-2) and register under the
    port's name; the ratchet is that no port may lack one except the baseline's, which only
    shrinks."""
    assert PORTS_WITHOUT_FAKE <= _CEILING, (
        "PORTS_WITHOUT_FAKE may only shrink: a new port needs a fake"
    )
    have = set(F.FAKES)
    missing = set(_ports()) - have
    assert missing <= PORTS_WITHOUT_FAKE, (
        f"a port without a fake: {sorted(missing - PORTS_WITHOUT_FAKE)}. Add a fake to "
        "ddflow/adapters/fakes and register it in FAKES; do not grow PORTS_WITHOUT_FAKE."
    )
    assert not (PORTS_WITHOUT_FAKE & have), (
        f"{sorted(PORTS_WITHOUT_FAKE & have)} now have a fake: remove them from "
        "PORTS_WITHOUT_FAKE so the ratchet shrinks."
    )
    assert have <= set(_ports()), f"a fake for no port: {sorted(have - set(_ports()))}"


def test_a_registered_fake_satisfies_its_runtime_checkable_port():
    for name, fake in F.FAKES.items():
        assert isinstance(fake(), getattr(P, name)), f"Fake{name} does not satisfy {name}"


def test_the_fakes_are_shipped_in_the_adapters_package():
    assert (PKG / "adapters" / "fakes" / "__init__.py").is_file()
    assert not (PKG / "fakes").exists(), "fakes live in ddflow/adapters/fakes, not ddflow/fakes"


# -- conformance: one set of cases, run against the adapter and its fake ----------------


def _real_adapters(monkeypatch=None) -> dict[str, object]:
    """The default adapter of each port that has a fake. Until a home moves under
    `ddflow.adapters` (ADP-migrate-homes) it is the existing module, which IS the port
    structurally: that is how a fake is dropped in where the module is imported."""
    from ddflow.core import digest

    out: dict[str, object] = {"Digest": digest}
    if monkeypatch is not None:
        # No network: the default adapter runs with urllib's `urlopen` scripted.
        from tests.test_adapters_http import ScriptedUrllib

        out["HttpClient"] = ScriptedUrllib(monkeypatch)
    return out


def _digest_cases(d) -> None:
    a = d.content_digest("hello")
    assert a == d.content_digest("hello") and a != d.content_digest("hellp")
    assert d.content_digest("hello", length=7) == a[:7]
    assert d.content_digest("hello", length=0) == ""
    assert d.content_digest("é") == d.content_digest("é".encode())
    assert d.canonical({"b": 1, "a": [1, 2]}) == d.canonical({"a": [1, 2], "b": 1})
    assert d.canonical({"a": 1}) != d.canonical({"a": 2})
    assert d.of_obj({"b": 1, "a": 2}) == d.of_obj({"a": 2, "b": 1})
    assert d.of_obj({"a": 1}) != d.of_obj({"a": 2})
    assert len(d.of_obj({"a": 1}, length=6)) == 6


def _http_cases(c) -> None:
    from tests.test_adapters_http import http_conformance

    http_conformance(c)


CASES = {"Digest": _digest_cases, "HttpClient": _http_cases}


def test_every_fake_has_a_conformance_case_and_a_real_adapter(monkeypatch):
    assert set(F.FAKES) == set(CASES) == set(_real_adapters(monkeypatch)), (
        "a fake with no conformance case runs against nothing: add its case here"
    )


@pytest.mark.parametrize("family", sorted(CASES))
def test_adapter_and_fake_pass_the_same_cases(family: str, monkeypatch):
    CASES[family](_real_adapters(monkeypatch)[family])
    CASES[family](F.FAKES[family]())


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


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_every_port_documents_its_home(family: str):
    """A port names where its default adapter lives, so the "where" half of the model is
    next to the code half, not only in the architecture document."""
    doc = _ports()[family].__doc__ or ""
    assert "ddflow.adapters." in doc, (
        f"{family} does not name its home in ddflow/adapters/: the port is the seam, and "
        "the doc says where the default lands."
    )
