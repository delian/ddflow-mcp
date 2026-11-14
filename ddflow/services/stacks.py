"""The stack table: per stack, the commands that build, smoke, lock-check and test a project.

One table, as data (`ddflow/templates/stacks/<stack>.toml`, plus a project layer under
`[verify].stacks_dir`), consumed by every stack-aware pass: the clean-clone pass, the
companion catalogue and probe. `resolve` answers, for a repository: which stack, which
command per role, and whether the program each starts with exists. Answers it cannot give
are explicit: a stack no entry describes is status `none` with the command that fixes it, a
missing tool is `unavailable` -- never another stack's command, never a pass.

Pure apart from reading the files named; PATH lookup is injected (`which`), so tests need no
real toolchain.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..adapters import fs as FS
from ..adapters.toml import DEFAULT as TOML

SHIPPED_DIR = Path(__file__).resolve().parents[1] / "templates" / "stacks"
#: The roles a stack declares a command for.
ROLES = ("build", "smoke", "lock_check", "test")
_SEPARATORS = re.compile(r"&&|\|\||;|\|")
_ASSIGN = re.compile(r"^\w+=")


@dataclass(frozen=True)
class Variant:
    """One command of a role: used when its `when` file exists (empty: always)."""

    run: str
    when: str = ""


@dataclass(frozen=True)
class Stack:
    name: str
    title: str
    detect: tuple[str, ...]
    priority: int = 50
    lock_files: tuple[str, ...] = ()
    comment_tokens: tuple[str, ...] = ()
    import_extractor: str = ""
    dead_tool: str = ""
    dep_tool: str = ""
    commands: dict[str, tuple[Variant, ...]] = field(default_factory=dict)
    source: str = "shipped"


@dataclass(frozen=True)
class Step:
    role: str
    command: str
    programs: tuple[str, ...]
    available: bool
    source: str  #: table | config
    missing: tuple[str, ...] = ()


@dataclass(frozen=True)
class Resolution:
    stack: str  #: "" when none
    status: str  #: resolved | unavailable | none
    steps: dict[str, Step] = field(default_factory=dict)
    detail: str = ""
    next_call: str = ""
    also: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    evidence: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "stack": self.stack,
            "status": self.status,
            "detail": self.detail,
            "next_call": self.next_call,
            "also": list(self.also),
            "warnings": list(self.warnings),
            "evidence": self.evidence,
            "steps": {
                r: {
                    "command": s.command,
                    "available": s.available,
                    "source": s.source,
                    "missing": list(s.missing),
                }
                for r, s in self.steps.items()
            },
        }


def overrides_of(v: Any) -> dict[str, str]:
    """The roles a `[verify]` knob overrides, and with what (empty ones left out)."""
    raw = {
        "build": v.build_cmd,
        "smoke": v.smoke_cmd,
        "lock_check": v.lock_check_cmd,
        "test": v.test_cmd,
    }
    return {r: c.strip() for r, c in raw.items() if c.strip()}


def programs_of(command: str) -> tuple[str, ...]:
    """The programs a shell command line starts: the first word of each `&&`/`;`/`|` segment,
    environment assignments skipped."""
    out: list[str] = []
    for seg in _SEPARATORS.split(command):
        words = [w for w in seg.split() if not _ASSIGN.match(w)]
        if words and words[0] not in out:
            out.append(words[0])
    return tuple(out)


def _variants(raw: Any) -> tuple[Variant, ...]:
    if isinstance(raw, str):
        return (Variant(raw),) if raw.strip() else ()
    out = []
    for entry in raw or ():
        if isinstance(entry, dict) and str(entry.get("run", "")).strip():
            out.append(Variant(str(entry["run"]), str(entry.get("when", ""))))
    return tuple(out)


def _build(data: dict[str, Any], source: str) -> Stack:
    s = data.get("stack") or {}
    name = str(s.get("name", "")).strip()
    detect = tuple(str(g) for g in s.get("detect") or ())
    if not name or not detect:
        raise ValueError("[stack] needs a name and at least one detect glob")
    cmds = {r: v for r in ROLES if (v := _variants((data.get("commands") or {}).get(r)))}
    return Stack(
        name=name,
        title=str(s.get("title", name)),
        detect=detect,
        priority=int(s.get("priority", 50)),
        lock_files=tuple(str(x) for x in s.get("lock_files") or ()),
        comment_tokens=tuple(str(x) for x in s.get("comment_tokens") or ()),
        import_extractor=str(s.get("import_extractor", "")),
        dead_tool=str(s.get("dead_tool", "")),
        dep_tool=str(s.get("dep_tool", "")),
        commands=cmds,
        source=source,
    )


def load_table(
    root: Path | None = None, stacks_dir: str = ""
) -> tuple[dict[str, Stack], list[str]]:
    """The shipped stacks, then the project's (same name replaces). Returns (table, warnings);
    a file that does not parse is a warning, not an error."""
    table: dict[str, Stack] = {}
    warnings: list[str] = []
    layers = [("shipped", SHIPPED_DIR)]
    if root is not None and stacks_dir:
        layers.append(("project", Path(root) / stacks_dir))
    for label, directory in layers:
        for f in sorted(directory.glob("*.toml")) if directory.is_dir() else []:
            try:
                st = _build(TOML.read(f), label)
            except (ValueError, OSError, TypeError) as exc:
                warnings.append(f"{label} stack table {f.name}: {exc}")
                continue
            table[st.name] = st
    return table, warnings


def _matches(stack: Stack, root: Path) -> str:
    """The first file in ``root`` a detect glob of ``stack`` finds ("" if none)."""
    for g in stack.detect:
        hit = next(iter(sorted(root.glob(g))), None)
        if hit is not None:
            return hit.name
    return ""


def _step(role: str, command: str, source: str, which: Callable[[str], str | None]) -> Step:
    progs = programs_of(command)
    missing = tuple(p for p in progs if which(p) is None)
    return Step(role, command, progs, not missing, source, missing)


def resolve(root: Path, cfg: Any, which: Callable[[str], str | None] = FS.which) -> Resolution:
    """What ``root`` builds, smokes, lock-checks and tests with, from the table and the
    `[verify]` overrides of ``cfg``."""
    root = Path(root)
    v = cfg.verify
    table, warns = load_table(root, v.stacks_dir)
    forced = v.stack.strip()
    chosen: Stack | None = None
    evidence = ""
    also: list[str] = []
    if forced:
        chosen = table.get(forced)
        evidence = "[verify].stack"
        if chosen is None:
            return _none(
                f"[verify].stack = {forced!r} names no stack in the table ({', '.join(sorted(table))})",
                tuple(warns),
                v,
                root,
                which,
            )
    else:
        found = [(st, hit) for st in table.values() if (hit := _matches(st, root))]
        found.sort(key=lambda p: (-p[0].priority, p[0].name))
        if found:
            chosen, hit = found[0]
            evidence = hit
            also = [st.name for st, _ in found[1:]]
    overrides = overrides_of(v)
    if chosen is None:
        return _none(
            "no stack in the table matches this repository's files",
            tuple(warns),
            v,
            root,
            which,
        )
    steps: dict[str, Step] = {}
    for role in ROLES:
        if role in overrides:
            steps[role] = _step(role, overrides[role], "config", which)
            continue
        for var in chosen.commands.get(role, ()):
            if not var.when or (root / var.when).exists():
                steps[role] = _step(role, var.run, "table", which)
                break
    gone = sorted({p for s in steps.values() for p in s.missing})
    build = steps.get("build")
    unavailable = build is None or not build.available
    detail = (
        f"{chosen.name}: not on PATH: {', '.join(gone)}"
        if gone
        else f"{chosen.name}: no build command"
        if build is None
        else ""
    )
    return Resolution(
        stack=chosen.name,
        status="unavailable" if unavailable else "resolved",
        steps=steps,
        detail=detail,
        also=tuple(also),
        warnings=tuple(warns),
        evidence=evidence,
    )


def _none(
    detail: str, warns: tuple[str, ...], v: Any, root: Path, which: Callable[[str], str | None]
) -> Resolution:
    """No stack: an explicit none, with the call that fixes it. A project may still have
    declared commands by `[verify]` knobs alone; those resolve as stack `custom`."""
    custom = overrides_of(v)
    if custom.get("build"):
        steps = {r: _step(r, c, "config", which) for r, c in custom.items()}
        build = steps["build"]
        return Resolution(
            stack="custom",
            status="resolved" if build.available else "unavailable",
            steps=steps,
            detail="commands from [verify] knobs"
            + (f"; not on PATH: {', '.join(build.missing)}" if build.missing else ""),
            warnings=warns,
            evidence="[verify]",
        )
    return Resolution(
        stack="",
        status="none",
        detail=detail,
        next_call="ddflow config verify.build_cmd '<your build command>' "
        "(or add .ddflow/stacks/<stack>.toml, see ddflow/templates/stacks/README.md)",
        warnings=warns,
    )
