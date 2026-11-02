"""The values the `ExternalTool` port speaks (D-adapters-answers: types sit next to the adapter).

Pure: only the standard library. `Finding` is the one normalized shape every scanner is read
into (`{tool, rule, severity, file, line, advisory}`, the shape SEC-scanners and TK-sarif use);
`ToolProbe` and `ToolRun` answer "is it there" and "what did it say"; `ToolRunner` is the small
seam through which a tool is run, injected by the caller so that this layer starts no process:
production wires it to `ddflow.services.cmdrunner` (`ddflow.services.toolrun`), a test wires a
script.

There is no "passed" here, on purpose. A run is `clean` (it ran, an exit code that means "nothing
found", no findings), `findings`, or `unavailable` (it could not be checked, for any reason).
`unavailable` is never read as clean: whoever consumes a `ToolRun` records it as unavailable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

#: `ToolRun.status` values. No `passed`: see the module docstring.
CLEAN, FINDINGS, UNAVAILABLE = "clean", "findings", "unavailable"

#: `RunResult.kind` when the run did not happen (the vocabulary of `services.cmdrunner`).
MISSING, TIMEOUT, COULD_NOT_RUN, NOT_FOUND, BUSY = (
    "missing",
    "timeout",
    "could_not_run",
    "not_found",
    "busy",
)


class ToolParseError(ValueError):
    """A tool's output was not in the shape its parser reads. Always becomes `unavailable`."""


@dataclass(frozen=True)
class Finding:
    """One result of a scanner, normalized. `advisory` is the advisory id (a CVE, a GHSA)
    where the tool names one, else empty; `line` is 0 when the tool gives none."""

    tool: str
    rule: str
    severity: str = ""
    file: str = ""
    line: int = 0
    advisory: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunResult:
    """What the injected runner returns. ``ran`` False means nothing was checked, and ``kind``
    (`MISSING`, `TIMEOUT`, ...) and ``reason`` say why; ``code`` is only meaningful when ran."""

    ran: bool
    code: int | None = None
    out: str = ""
    err: str = ""
    kind: str = ""
    reason: str = ""


class ToolRunner(Protocol):
    """Run ``argv`` for at most ``timeout_s`` seconds; never raise for the command's own trouble.

    The one way an `ExternalTool` starts anything. Production: `ddflow.services.toolrun`
    (over `cmdrunner`, so missing, timeout and could-not-run all arrive as ``ran=False``).
    """

    def __call__(
        self, argv: Sequence[str], *, timeout_s: float, cwd: str | None = None
    ) -> RunResult: ...


@dataclass
class ToolProbe:
    """Is the tool there, and which version. ``installed`` is three-valued like the
    companions' probe: True (it answered), False (a FACT: not on PATH, or it said no), None
    (could not tell: timeout, could not start). ``pinned_ok`` is None when no pin is set or no
    version could be read. ``hint`` is the install command, as data (never run)."""

    tool: str
    installed: bool | None
    version: str = ""
    pin: str = ""
    pinned_ok: bool | None = None
    reason: str = ""
    hint: str = ""

    @property
    def usable(self) -> bool:
        """Installed, and the pin (when one is set) is KNOWN to be met. Not knowing is not usable."""
        if self.pin:
            return self.installed is True and self.pinned_ok is True
        return self.installed is True


@dataclass
class ToolRun:
    """How one run ended: ``status`` is `CLEAN`, `FINDINGS` or `UNAVAILABLE` (``reason`` says why
    nothing was checked, and what to do next)."""

    tool: str
    status: str
    code: int | None = None
    findings: tuple[Finding, ...] = ()
    reason: str = ""
    kind: str = ""
    out: str = ""
    err: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def unavailable(self) -> bool:
        return self.status == UNAVAILABLE

    @property
    def clean(self) -> bool:
        return self.status == CLEAN


@dataclass(frozen=True)
class ToolSpec:
    """A tool described as data: its row in `tools.toml`.

    ``argv`` is the run command (extra arguments are appended), ``version_argv`` the version
    probe, ``version_re`` the first capture group of which is the version, ``parser`` the name
    of the output parser, ``clean_codes`` the exit codes that mean it ran and found nothing,
    ``finding_codes`` those that mean it ran and found something (anything else is unavailable),
    ``pin`` a minimum version ("" none) and ``install`` the command a human runs, shown only.
    """

    id: str
    argv: tuple[str, ...]
    version_argv: tuple[str, ...]
    parser: str = "none"
    version_re: str = r"(\d+(?:\.\d+)+)"
    clean_codes: tuple[int, ...] = (0,)
    finding_codes: tuple[int, ...] = ()
    pin: str = ""
    install: str = ""
    title: str = ""
