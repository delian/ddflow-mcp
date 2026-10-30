"""E4 render_argv: a launch command from an argv template. No shell, no splitting.

A placeholder is a whole argv element; its value becomes exactly one argument, whatever it
holds. The placeholder names are a closed list, the executable is never a placeholder and
never a shell, and secrets travel by env var NAME (the value is read at launch and kept out
of ``repr`` and ``describe``).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from .safety import DEFAULT, EngineError, Limits

PLACEHOLDERS = frozenset(
    {"model", "session", "prompt_file", "dir", "effort", "max_turns", "mcp_config"}
)
SHELLS = frozenset(
    {"sh", "bash", "zsh", "dash", "fish", "ksh", "csh", "tcsh", "cmd", "powershell", "pwsh", "env"}
)
_PH = re.compile(r"\{([a-z_]+)\}")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")


@dataclass(frozen=True)
class Rendered:
    argv: tuple[str, ...]
    env: dict[str, str] = field(default_factory=dict, repr=False)  #: child var -> value
    missing: tuple[str, ...] = ()  #: source env names that were not set

    def describe(self) -> dict:
        """The argv and the env NAMES; values are never included."""
        return {"argv": list(self.argv), "env": sorted(self.env), "missing": list(self.missing)}


def is_shell(program: str) -> bool:
    """True for a shell or ``env`` by program name: either path separator, any case, ``.exe`` ignored."""
    base = re.split(r"[\\/]", program)[-1].lower()
    return base.removesuffix(".exe") in SHELLS


def _lint(template: Sequence[str], limits: Limits) -> None:
    if not isinstance(template, (list, tuple)) or not template:
        raise EngineError("an argv template is a non-empty list of strings")
    if len(template) > limits.max_argv:
        raise EngineError(f"argv longer than {limits.max_argv} elements")
    for i, el in enumerate(template):
        if not isinstance(el, str) or "\0" in el or len(el) > limits.max_value_chars:
            raise EngineError(
                f"argv[{i}]: expected a string without NUL, at most {limits.max_value_chars} characters"
            )
        if "{" in el or "}" in el:
            m = _PH.fullmatch(el)
            if not m:
                raise EngineError(f"argv[{i}]: {el[:40]!r}: a placeholder must be a whole element")
            if m.group(1) not in PLACEHOLDERS:
                raise EngineError(
                    f"argv[{i}]: unknown placeholder {el}; closed list: {sorted(PLACEHOLDERS)}"
                )
            if i == 0:
                raise EngineError("the executable cannot be a placeholder")
    if not template[0]:
        raise EngineError("the executable is empty")
    if is_shell(template[0]):
        raise EngineError(
            f"{template[0]!r} is a shell: launches are argv lists, never a shell line"
        )


def render(
    template: Sequence[str],
    values: Mapping[str, str],
    *,
    env: Mapping[str, str] | None = None,
    environ: Mapping[str, str] | None = None,
    limits: Limits = DEFAULT,
) -> Rendered:
    """``template`` with each placeholder element replaced by its value (one argument)."""
    _lint(template, limits)
    argv: list[str] = []
    for el in template:
        m = _PH.fullmatch(el)
        if not m:
            argv.append(el)
            continue
        val = values.get(m.group(1))
        if val is None:
            raise EngineError(f"no value for placeholder {el}")
        val = str(val)
        if "\0" in val or len(val) > limits.max_value_chars:
            raise EngineError(f"the value for {el} has a NUL or is too long")
        argv.append(val)
    out: dict[str, str] = {}
    missing: list[str] = []
    for child, source in (env or {}).items():
        if (
            not isinstance(child, str)
            or not _ENV_NAME.fullmatch(child)
            or not isinstance(source, str)
            or not _ENV_NAME.fullmatch(source)
        ):
            raise EngineError(
                f"env {child!r}: names must be [A-Za-z_][A-Za-z0-9_]*; secrets go by name"
            )
        if source in (environ or {}):
            out[child] = environ[source]  # type: ignore[index]
        else:
            missing.append(source)
    return Rendered(tuple(argv), out, tuple(missing))
