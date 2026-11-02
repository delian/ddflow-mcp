"""The shipped fake of the `ExternalTool` port: scripted exit codes and outputs, no process."""

from __future__ import annotations

import re

from ..tool_parsers import PARSERS
from ..tool_types import (
    CLEAN,
    COULD_NOT_RUN,
    FINDINGS,
    MISSING,
    NOT_FOUND,
    UNAVAILABLE,
    Finding,
    RunResult,
    ToolParseError,
    ToolProbe,
    ToolRun,
)
from . import fake


@fake
class FakeExternalTool:
    """A scripted `ExternalTool`: ``probe`` and ``run`` are the `RunResult`s the "process" gave
    (an exit code and output, or ``RunResult(False, kind=...)`` for one that did not happen), and
    the fake maps them as the default adapter does -- every failure is `unavailable`, never clean.

    Output is read with the ``parser`` named (default `normalized`, a JSON array of
    ``{rule, severity, file, line, advisory}``), so a test scripts findings as plain JSON.
    No process is started and nothing is installed.
    """

    def __init__(
        self,
        name: str = "fake",
        *,
        probe: RunResult | None = None,
        run: RunResult | None = None,
        parser: str = "normalized",
        clean_codes: tuple[int, ...] = (0,),
        finding_codes: tuple[int, ...] = (),
        install: str = "install-fake-tool",
    ) -> None:
        self.name = name
        self._probe = probe or RunResult(True, 0, "1.0.0")
        self._run = run or RunResult(True, 0, "[]")
        self._parser = parser
        self._clean, self._finding = clean_codes, finding_codes
        self._install = install
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def tool_id(self) -> str:
        return self.name

    def install_hint(self) -> str:
        return self._install

    def parse(self, output: str) -> list[Finding]:
        return PARSERS[self._parser](self.name, output)

    def probe(self) -> ToolProbe:
        self.calls.append(("probe", ()))
        p = self._probe
        if not p.ran:
            absent = p.kind in (MISSING, NOT_FOUND)
            return ToolProbe(
                self.name, False if absent else None, reason=p.reason, hint=self._install
            )
        if p.code != 0:
            return ToolProbe(self.name, False, reason=f"exited {p.code}", hint=self._install)
        m = re.search(r"\d+(?:\.\d+)+", p.out + "\n" + p.err)
        return ToolProbe(self.name, True, m.group(0) if m else "", hint=self._install)

    def run(self, args: list[str] | None = None, *, cwd: str | None = None) -> ToolRun:
        self.calls.append(("run", tuple(args or ())))
        r = self._run

        def down(kind: str, why: str) -> ToolRun:
            return ToolRun(self.name, UNAVAILABLE, r.code, (), why, kind, r.out, r.err)

        if not r.ran:
            return down(
                r.kind or COULD_NOT_RUN, f"{r.reason}. Ask the operator, then run: {self._install}"
            )
        if r.code not in (*self._clean, *self._finding):
            return down(COULD_NOT_RUN, f"exited {r.code}: unexpected; record unavailable")
        try:
            found = tuple(self.parse(r.out))
        except ToolParseError as exc:
            return down(COULD_NOT_RUN, f"could not parse output: {exc}")
        if r.code in self._finding and not found:
            return down(COULD_NOT_RUN, "a findings exit with no findings readable")
        return ToolRun(self.name, FINDINGS if found else CLEAN, r.code, found, "", "", r.out, r.err)

    def scripted(self, probe: RunResult | None = None, run: RunResult | None = None):
        """A copy of this fake that answers ``probe`` and ``run`` (the conformance cases and
        tests script one tool this way)."""
        return type(self)(
            self.name,
            probe=probe or self._probe,
            run=run or self._run,
            parser=self._parser,
            clean_codes=self._clean,
            finding_codes=self._finding,
            install=self._install,
        )
