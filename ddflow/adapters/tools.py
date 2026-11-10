"""The default `ExternalTool` adapter: one table-described implementation for every tool.

A tool is a row in `tools.toml` (`ToolSpec`); `TableTool` is the one class that probes it, runs it
through an INJECTED runner, parses its output with a named parser and maps every way it can fail
to `unavailable`. A new scanner is a new row (and a parser in `tool_parsers` when its output is
not one already there); no caller writes its own probe, version check or "not installed" rule.

This layer starts no process (`ddflow.adapters` is the bottom of the layer contract and only
`infra.proc` and `infra.git` may use `subprocess`): production passes
`ddflow.services.toolrun.command_tool_runner()`, which runs the argv through `cmdrunner`. See
`docs/ARCHITECTURE.md`, "External tools".

`install_hint()` is data: the command a human runs after the operator agrees. Nothing here runs it.
"""

from __future__ import annotations

import re
import tomllib
from importlib import resources
from typing import Any, Protocol, runtime_checkable

from .ports import port
from .registry import Registry
from .tool_parsers import PARSERS
from .tool_types import (
    CLEAN,
    COULD_NOT_RUN,
    FINDINGS,
    MISSING,
    NOT_FOUND,
    UNAVAILABLE,
    Finding,
    RunResult,
    ToolProbe,
    ToolRun,
    ToolRunner,
    ToolSpec,
)


@port
@runtime_checkable
class ExternalTool(Protocol):
    """A command-line tool ddflow shells to (roborev, gitleaks, pre-commit, ...), reached
    through one probe, one run, one parser and one "unavailable" rule.

    One adapter per tool, each answering `tool_id()` (a method, not a `name` member: a data
    member would stop the port satisfying `issubclass`), described as a row of `tools.toml` and
    held in `ddflow.adapters.tools.SPECS`. Home:
    `ddflow.adapters.tools` (values in `ddflow.adapters.tool_types`). The adapter starts no
    process itself: the caller injects a runner (`ddflow.services.toolrun` in production).
    Results are typed `Any` here: `ToolProbe`, `ToolRun` and `Finding` stay in the adapter's
    own module. A failure is never "passed": every one is a `ToolRun` whose status is
    `unavailable`.
    """

    def tool_id(self) -> str:
        """The tool's id: the row of `tools.toml` it is described by."""

    def probe(self) -> Any:
        """Installed (True, False, or None for could-not-tell), version, pin satisfied; a
        `ToolProbe`."""

    def run(self, args: list[str] | None = None, *, cwd: str | None = None) -> Any:
        """Run with `args` appended; a `ToolRun` that is `clean`, `findings` or `unavailable`."""

    def parse(self, output: str) -> list[Any]:
        """The tool's output as normalized `Finding`s; `ToolParseError` for a shape not read."""

    def install_hint(self) -> str:
        """The install command as data: shown to the operator, never run without consent."""


#: Defaults the caller overrides (production reads them from configuration): seconds a version
#: probe and a run may take.
DEFAULT_PROBE_TIMEOUT_S = 20.0
DEFAULT_RUN_TIMEOUT_S = 300.0

#: How much of a tool's stderr a reason quotes.
_ERR_TAIL = 200

_NUM = re.compile(r"\d+")


def _row_tuple(row: dict[str, Any], key: str, cast: type, default: tuple = ()) -> tuple:
    value = row.get(key, default)
    if not isinstance(value, list | tuple) or not all(isinstance(v, cast) for v in value):
        raise ValueError(f"tool {row.get('id')!r}: {key} must be a list of {cast.__name__}")
    return tuple(value)


def spec_from_row(row: dict[str, Any]) -> ToolSpec:
    """A `ToolSpec` from one `[[tool]]` row; a row that cannot work is refused when it is
    read, not when a gate first reaches for it."""
    tid = str(row.get("id", "")).strip()
    if not tid:
        raise ValueError("a [[tool]] row needs an id")
    argv, version_argv = _row_tuple(row, "argv", str), _row_tuple(row, "version_argv", str)
    if not argv or not version_argv:
        raise ValueError(f"tool {tid!r} needs argv and version_argv")
    parser = str(row.get("parser", "none"))
    if parser not in PARSERS:
        raise ValueError(f"tool {tid!r}: unknown parser {parser!r}. Known: {', '.join(PARSERS)}")
    try:
        re.compile(str(row.get("version_re", r"(\d+)")))
    except (re.error, OverflowError, RecursionError) as exc:
        raise ValueError(f"tool {tid!r}: version_re is not a regular expression: {exc}") from exc
    extra = {k: str(row[k]) for k in ("version_re", "pin", "install", "title") if k in row}
    return ToolSpec(
        tid,
        argv,
        version_argv,
        parser,
        clean_codes=_row_tuple(row, "clean_codes", int, (0,)),
        finding_codes=_row_tuple(row, "finding_codes", int),
        **extra,
    )


def _load_table() -> list[ToolSpec]:
    text = resources.files(__package__).joinpath("tools.toml").read_text("utf-8")
    return [spec_from_row(r) for r in tomllib.loads(text).get("tool", [])]


#: Every described tool, by id. Register another with `SPECS.register(spec)`.
SPECS: Registry[ToolSpec] = Registry("external tool", key=lambda s: s.id, loader=_load_table)


_CORE = re.compile(r"v?(\d+(?:\.\d+)*)([^\s+]*)")
_PRE = re.compile(r"^[-._]?(?:a|b|c|rc|alpha|beta|dev|pre|preview)(?:[-._]?\d+)?\b", re.IGNORECASE)


def _version_key(text: str) -> tuple[tuple[int, ...], str]:
    """(the numeric core, the pre-release tag or ""): "8.30.0-rc.1" is ((8, 30, 0), "-rc.1")."""
    m = _CORE.search(text.strip())
    if not m:
        return (), ""
    return tuple(int(n) for n in m.group(1).split(".")), (
        m.group(2) if _PRE.match(m.group(2)) else ""
    )


def version_at_least(version: str, pin: str) -> bool | None:
    """Is ``version`` >= ``pin``, comparing dotted numbers part by part ("8.30.10" > "8.30.1",
    "8.30" == "8.30.0"); a pre-release ("8.30.0-rc.1") is older than its release, and build
    metadata ("+build.1") is ignored. None when
    either holds no number: not knowing is not a pass."""
    (have, pre), (want, want_pre) = _version_key(version), _version_key(pin)
    if not have or not want:
        return None
    size = max(len(have), len(want))
    have, want = have + (0,) * (size - len(have)), want + (0,) * (size - len(want))
    if have != want:
        return have > want
    # Same core: a release satisfies anything; two pre-releases are ordered only when identical
    # (their order is not read, and not knowing is not a pass).
    return not pre or pre == want_pre


class TableTool:
    """An `ExternalTool` described by a `ToolSpec` and run through ``runner``."""

    def __init__(
        self,
        spec: ToolSpec,
        runner: ToolRunner,
        *,
        pin: str | None = None,
        probe_timeout_s: float = DEFAULT_PROBE_TIMEOUT_S,
        run_timeout_s: float = DEFAULT_RUN_TIMEOUT_S,
    ) -> None:
        self.spec = spec
        self.runner = runner
        self.pin = spec.pin if pin is None else pin
        self.probe_timeout_s = probe_timeout_s
        self.run_timeout_s = run_timeout_s
        self._probed: ToolProbe | None = None

    # -- the port ------------------------------------------------------------------------
    def tool_id(self) -> str:
        return self.spec.id

    def install_hint(self) -> str:
        """The install command, as data. Show it; run it only when the operator agrees."""
        return self.spec.install

    def probe(self) -> ToolProbe:
        """Is it installed, which version, is the pin met. Read-only, bounded, three-valued."""
        s = self.spec
        done = self._call(s.version_argv, self.probe_timeout_s, None)
        mk = lambda installed, reason="", version="": ToolProbe(  # noqa: E731
            s.id, installed, version, self.pin, None, reason, s.install
        )
        if not done.ran:
            if done.kind in (MISSING, NOT_FOUND):
                return mk(False, self._absent(done.reason))
            return mk(
                None,
                f"{done.reason or 'the probe did not run'} -- could not tell, which is not the "
                f"same as absent. Run `{' '.join(s.version_argv)}` by hand, then probe again.",
            )
        if done.code != 0:
            return mk(
                False,
                f"`{' '.join(s.version_argv)}` exited {done.code}: {done.err.strip()[:_ERR_TAIL]}. "
                f"Not usable as installed; ask the operator, then run: {s.install}",
            )
        m = re.search(s.version_re, done.out + "\n" + done.err)
        version = ((m.group(1) if m.groups() else m.group(0)) if m else "") or ""
        version = version.strip()
        out = mk(True, version=version)
        out.pinned_ok = version_at_least(version, self.pin) if self.pin else None
        if out.pinned_ok is False:
            out.reason = (
                f"{s.id} {version} is older than the pinned {self.pin}. Ask the operator, then "
                f"upgrade with: {s.install}"
            )
        return out

    def parse(self, output: str) -> list[Finding]:
        """The tool's output as normalized findings; `ToolParseError` for a shape not read."""
        return PARSERS[self.spec.parser](self.spec.id, output)

    def run(
        self,
        args: list[str] | tuple[str, ...] | None = None,
        *,
        cwd: str | None = None,
        timeout_s: float | None = None,
    ) -> ToolRun:
        """Run the tool with ``args`` appended to its argv. `unavailable` for anything that is
        not a clean answer: the tool absent, a timeout, an exit code the row does not know, or
        output the parser cannot read. With a pin set, an unmet pin is unavailable too."""
        s = self.spec
        if self.pin:
            # The pin is enforced against the binary as it is NOW, so it is probed fresh on every
            # pinned run: a cached probe is either a stale success (the binary was replaced, so a
            # pin that is no longer met reads as met) or a transient failure (a timeout) that would
            # otherwise freeze into every later run. One bounded probe per pinned run is the cost.
            self._probed = self.probe()
            if not self._probed.usable:
                return self._unavailable(
                    "pin",
                    self._probed.reason or f"{s.id} could not be probed against pin {self.pin}",
                )
        argv = [*s.argv, *(args or ())]
        done = self._call(argv, self.run_timeout_s if timeout_s is None else timeout_s, cwd)
        if not done.ran:
            reason = (
                self._absent(done.reason)
                if done.kind in (MISSING, NOT_FOUND)
                else (
                    f"{done.reason or 'the tool did not run'}. Record the gate unavailable; run "
                    f"`{' '.join(argv)}` by hand to see why."
                )
            )
            return self._unavailable(done.kind or COULD_NOT_RUN, reason, done)
        if done.code not in (*s.clean_codes, *s.finding_codes):
            return self._unavailable(
                COULD_NOT_RUN,
                f"`{' '.join(argv)}` exited {done.code}, a code {s.id} does not use for a result "
                f"({done.err.strip()[:_ERR_TAIL] or 'no stderr'}). Run it by hand to see why; "
                "until it answers, record the gate unavailable.",
                done,
            )
        try:
            found = tuple(self.parse(done.out))
        except Exception as exc:  # a parser that raises anything is "could not read it"
            return self._unavailable(
                COULD_NOT_RUN,
                f"could not parse {s.id} output: {self._why(exc)}. Run `{' '.join(argv)}` by hand and "
                "compare with `ddflow/adapters/tool_parsers.py`; until then record unavailable.",
                done,
            )
        if done.code in s.finding_codes and not found:
            return self._unavailable(
                COULD_NOT_RUN,
                f"{s.id} exited {done.code} (it found something) but no finding could be read "
                f"from its output. Run `{' '.join(argv)}` by hand; until then record unavailable.",
                done,
            )
        return ToolRun(
            s.id, FINDINGS if found else CLEAN, done.code, found, "", "", done.out, done.err
        )

    # -- helpers -------------------------------------------------------------------------
    def _call(self, argv: tuple[str, ...] | list[str], timeout_s: float, cwd: str | None):
        try:
            return self.runner(list(argv), timeout_s=timeout_s, cwd=cwd)
        except Exception as exc:  # a runner that raises must not turn into a crash or a pass
            return RunResult(False, None, kind=COULD_NOT_RUN, reason=f"the runner failed: {exc}")

    @staticmethod
    def _why(exc: Exception) -> str:
        """The exception's TYPE only: its text may hold a line of what the tool printed (a
        secret), and a parser's message is not ours to vouch for."""
        return type(exc).__name__

    def _absent(self, reason: str) -> str:
        return (
            f"{reason or self.spec.id + ' is not installed'}. Ask the operator, then run: "
            f"{self.spec.install}. Until then record the gate unavailable."
        )

    def _unavailable(self, kind: str, reason: str, done: RunResult | None = None) -> ToolRun:
        return ToolRun(
            self.spec.id,
            UNAVAILABLE,
            done.code if done else None,
            (),
            reason,
            kind,
            done.out if done else "",
            done.err if done else "",
        )


def tool(tool_id: str, runner: ToolRunner, **options: Any) -> TableTool:
    """The adapter for ``tool_id`` (a row of `tools.toml`, or one added to `SPECS`) over
    ``runner``. ``options``: `pin`, `probe_timeout_s`, `run_timeout_s`."""
    return TableTool(SPECS[tool_id], runner, **options)
