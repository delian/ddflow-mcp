"""`ddflow doctor`: completion is registered but the engine does not answer.

The shell registration is a line in a startup file (`ddflow completion install`); a TAB then
runs the ``ddflow`` found on PATH with argcomplete's environment. If that program does not
answer (a build that never wired the engine, a wrapper that swallows the request) every TAB
offers nothing and nothing says why -- the failure this check names. It speaks only when a
registration exists, asks the way the shell does (``COMP_LINE='ddflow comple'``), and reads
the answer from the tempfile argcomplete supports. Advisory: a note, never a failure, and
silence when nothing is registered.
"""

from __future__ import annotations

from pathlib import Path

from ..adapters import env as E
from ..adapters import fs as FS
from ..adapters import fsio
from ..adapters import proc as P
from .completion import install as I

SHELLS = ("bash", "zsh", "fish")
#: What the probe types and the word it must be offered.
PROBE_LINE, PROBE_WANT = "ddflow comple", "completion"
#: Seconds the probe may take (a TAB is one process start plus the parser build).
TIMEOUT_S = 30.0


def registered(home: Path) -> list[Path]:
    """The startup files under ``home`` that carry the ddflow completion block."""
    found: list[Path] = []
    for shell in SHELLS:
        path = I.target_file(shell, home)
        try:
            current = path.read_text(encoding="utf-8")
        except (OSError, ValueError):  # unreadable, or not text
            continue
        if I.uninstall_text(shell, current) != current:
            found.append(path)
    return found


def answers(exe: str) -> tuple[bool, str]:
    """Does ``exe`` answer a completion request for ``PROBE_LINE``? (verdict, what it said)"""
    try:
        with fsio.temp_text("") as out:
            env = {
                **E.SystemEnv().snapshot(),
                "_ARGCOMPLETE": "1",
                "_ARGCOMPLETE_IFS": "\x0b",
                "_ARGCOMPLETE_STDOUT_FILENAME": str(out),
                "_ARGCOMPLETE_SHELL": "bash",
                "COMP_LINE": PROBE_LINE,
                "COMP_POINT": str(len(PROBE_LINE)),
            }
            done = P.capture([exe], timeout=TIMEOUT_S, env=env)
            words = out.read_text(encoding="utf-8").replace("\x0b", " ").split()
    except OSError:
        return False, "no answer"
    if done.error is not None:
        return False, f"it did not finish ({type(done.error).__name__})"
    return PROBE_WANT in words, " ".join(words) or "nothing"


def notes(home: Path | None = None) -> list[str]:
    """The doctor note(s): empty when no registration exists or the engine answers."""
    files = registered(home or Path.home())
    if not files:
        return []
    where = ", ".join(str(f) for f in files)
    exe = FS.which("ddflow")
    if exe is None:
        return [
            f"shell completion is registered in {where} but no `ddflow` is on PATH, so a TAB "
            "runs nothing: put ddflow on PATH, or `ddflow completion uninstall`"
        ]
    ok, said = answers(exe)
    if ok:
        return []
    return [
        f"shell completion is registered in {where} but `{exe}` does not answer it: asked "
        f"`{PROBE_LINE}<TAB>`, it said {said}. This ddflow predates the completion engine or "
        "its entry point is stale: upgrade or reinstall ddflow (`uv tool install --reinstall "
        "ddflow`, or `uv sync` in a source checkout)"
    ]
