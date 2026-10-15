"""Constants audit (D-everything-configurable rule 6): find every tunable module-level
constant and say whether it is a knob still to migrate, an exemption with a reason, or
unclassified.

A *candidate* is a module-level assignment of an int/float literal to an UPPER_CASE name
(or literal arithmetic such as `30 * 60`) whose words say it is a tunable (a timeout, a limit, a cap, a window, a threshold ...).
The register (`docs/ddflow/constants-exemptions.toml`) lists each as `[knob]` (to migrate;
the value is the proposed config key) or `[exempt]` (structural, with the reason). The
ratchet (tests/test_constants_ratchet.py) fails a NEW unclassified candidate and a stale
register entry, so the register only shrinks.
"""

from __future__ import annotations

import ast
import operator
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

#: Name fragments that make a constant a tunable (matched on `_`-separated words, or `_S`).
TUNABLE = re.compile(
    r"(?:^|_)(?:S|MS|TIMEOUT|MAX|MIN|LIMIT|DEFAULT|WINDOW|THRESHOLD|RETRIES|RETRY|CAP|"
    r"INTERVAL|TTL|BUDGET|DELAY|BACKOFF|SIZE|DEPTH|PERIOD|POLL|EVERY|GRACE|WAIT|"
    r"RECHECK|LIVE|STALE|TOUCH|BYTES|CHARS|LINES|DAYS|FILES|PAGES|HOPS|FIRES)(?:_|$)"
)

#: Where the register lives, relative to the repository root.
REGISTER = Path("docs/ddflow/constants-exemptions.toml")


@dataclass(frozen=True)
class Candidate:
    path: str  # relative to the repository root, '/'-separated
    name: str
    value: float
    line: int

    @property
    def key(self) -> str:
        return f"{self.path}:{self.name}"


#: Arithmetic over literals is still a literal: `30 * 60.0`, `1 << 20`, `7 * 86400`.
_FOLD_BOUND = 64
_FOLD = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.FloorDiv: operator.floordiv,
    ast.Div: operator.truediv,
    ast.Pow: lambda a, b: a**b if abs(b) <= _FOLD_BOUND else None,  # no runaway exponents
    ast.LShift: lambda a, b: int(a) << int(b) if 0 <= b <= _FOLD_BOUND else None,
}


def _number(node: ast.expr | None) -> float | None:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _number(node.operand)
        return None if inner is None else -inner
    if isinstance(node, ast.BinOp) and type(node.op) in _FOLD:
        left, right = _number(node.left), _number(node.right)
        if left is None or right is None:
            return None
        try:
            folded = _FOLD[type(node.op)](left, right)
        except (ArithmeticError, TypeError, ValueError):
            return None
        return folded if isinstance(folded, (int, float)) else None  # not complex
    if isinstance(node, ast.Constant) and not isinstance(node.value, bool):
        if isinstance(node.value, (int, float)):
            return node.value
    return None


def candidates_in(source: str, path: str) -> list[Candidate]:
    """The candidates among ``source``'s module-level statements (not nested ones)."""
    out: list[Candidate] = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        number = _number(value)
        if number is None:
            continue
        for t in targets:
            name = t.id if isinstance(t, ast.Name) else ""
            bare = name.lstrip("_")
            if bare and bare.isupper() and TUNABLE.search(bare):
                out.append(Candidate(path, name, number, node.lineno))
    return out


def scan(root: Path) -> list[Candidate]:
    """Every candidate under ``root``/ddflow, in path then line order."""
    out: list[Candidate] = []
    for p in sorted((root / "ddflow").rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(root).as_posix()
        out.extend(candidates_in(p.read_text("utf-8"), rel))
    return out


@dataclass(frozen=True)
class Register:
    knob: dict[str, str]  # key -> proposed config key
    exempt: dict[str, str]  # key -> reason


def load_register(root: Path) -> Register:
    data = tomllib.loads((root / REGISTER).read_text("utf-8"))
    return Register(dict(data.get("knob", {})), dict(data.get("exempt", {})))


@dataclass(frozen=True)
class Audit:
    knob: list[Candidate]
    exempt: list[Candidate]
    unclassified: list[Candidate]
    stale: list[str]  # register keys no candidate matches any more
    both: list[str]  # keys listed as knob and as exemption
    unreasoned: list[str]  # exemptions with an empty reason


def audit(root: Path, found: list[Candidate] | None = None) -> Audit:
    found = scan(root) if found is None else found
    reg = load_register(root)
    keys = {c.key for c in found}
    return Audit(
        knob=[c for c in found if c.key in reg.knob],
        exempt=[c for c in found if c.key in reg.exempt and c.key not in reg.knob],
        unclassified=[c for c in found if c.key not in reg.knob and c.key not in reg.exempt],
        stale=sorted((set(reg.knob) | set(reg.exempt)) - keys),
        both=sorted(set(reg.knob) & set(reg.exempt)),
        unreasoned=sorted(k for k, why in reg.exempt.items() if not why.strip()),
    )


def visibility(path: str) -> int:
    """1 = a person meets it (api, surfaces, views, config), 2 = services, 3 = core/infra."""
    layer = path.split("/")[1]
    return {"api": 1, "surfaces": 1, "views": 1, "config_sections": 1, "services": 2}.get(layer, 3)


def report(root: Path, found: list[Candidate] | None = None) -> str:
    a = audit(root, found)
    reg = load_register(root)
    lines = [
        f"constants audit: {len(a.knob)} knob, {len(a.exempt)} exempt, "
        f"{len(a.unclassified)} unclassified",
        "",
        "knobs to migrate, most user-visible first (file:name = value -> proposed key):",
    ]
    for c in sorted(a.knob, key=lambda c: (visibility(c.path), c.key)):
        lines.append(f"  [{visibility(c.path)}] {c.key} = {c.value:g} -> {reg.knob[c.key]}")
    if a.unclassified:
        lines += ["", "UNCLASSIFIED (make it a knob, or list it in the register with a reason):"]
        lines += [f"  {c.key}:{c.line} = {c.value:g}" for c in a.unclassified]
    for label, items in (
        ("stale entries", a.stale),
        ("both lists", a.both),
        ("exemption without a reason", a.unreasoned),
    ):
        if items:
            lines += ["", f"{label}:"] + [f"  {k}" for k in items]
    return "\n".join(lines)


def problems(a: Audit) -> bool:
    return bool(a.unclassified or a.stale or a.both or a.unreasoned)
