"""`SshTransport`: `remote.LaneTransport` over the operator's own ssh login (SW-remote-config split).

It sat here apart from `remote` so `remote` stays under the file-size limit (r-small-files): ssh's
own quoting, the shell recipe and the ssh argv stay one cohesive unit, and `remote` keeps the
built-in verbs plus the knobs. Nothing is forwarded, and an unknown host key or a password prompt
fails, whatever the ssh config says. The remote needs `sh`, `setsid` and git. What it answers is
data.
"""

from __future__ import annotations

import contextlib
import re
import shlex
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath

from ...infra import proc as P
from ..lane_driver import LaneStatus, Result, failed, ok, unavailable
from .remote_config import DEFAULT, Knobs, RemoteError, _safe

_HOST = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.@%:\[\]-]*")
_ROOT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._/-]*")
_SIGNALS = ("TERM", "INT", "HUP", "KILL")
_SSH_LOST = 255  # ssh's own failure, as against the remote command's exit code
_CHUNK = 65536

#: The start time `start` wrote proves the pid is the lane's, so a recycled pid is never
#: signalled. Without ``lstart`` no lane could be stopped: `start` refuses.
_STAMP = 'ps -o lstart= -p "$(cat lane.pid)"'
_ALIVE = f'[ ! -s lane.rc ] && [ -s lane.stamp ] && [ "$({_STAMP})" = "$(cat lane.stamp)" ]'
_OBSERVE = f"""if {_ALIVE}; then echo "running $(cat lane.pid)"
elif [ -s lane.rc ]; then echo "ended $(cat lane.rc)"
elif [ -f lane.stopped ]; then echo stopped
elif [ -f lane.pid ]; then echo gone
else echo absent; fi"""


class SshTransport:
    """`remote.LaneTransport` over the operator's own ssh login: nothing is forwarded, and an
    unknown host key or a password prompt fails, whatever the ssh config says. The remote
    needs ``sh``, ``setsid`` and git. What it answers is data."""

    def __init__(self, host: str, knobs: Knobs = DEFAULT) -> None:
        if not _HOST.fullmatch(host):
            raise RemoteError(f"ssh host {host!r} is not a plain host name")
        root = PurePosixPath(knobs.lanes_root)
        if root.is_absolute() or ".." in root.parts or not _ROOT.fullmatch(knobs.lanes_root):
            raise RemoteError(f"lanes_root {knobs.lanes_root!r} must stay under the remote home")
        self.target, self._k = host, knobs

    def command(self, shell_line: str) -> tuple[str, ...]:
        """The ssh argv that runs ``shell_line`` under ``sh``, whatever the login shell is."""
        k = self._k
        opts = ("BatchMode=yes", "StrictHostKeyChecking=yes")
        opts += (f"ConnectTimeout={k.ssh_connect_timeout_s}", "ClearAllForwardings=yes")
        opts += ("PermitLocalCommand=no", "ControlPath=none")
        flags = [x for o in opts for x in ("-o", o)]
        line = f"sh -c {shlex.quote(shell_line)}"
        return (*k.ssh_argv, "-T", "-a", "-x", *flags, "--", self.target, line)

    def _dir(self, lane: str) -> str:
        return shlex.quote(f"{self._k.lanes_root}/{_safe(lane)}")

    def _file(self, lane: str, name: str) -> str:
        return f"{self._dir(lane)}/{shlex.quote(_safe(name))}"

    def _judge(self, code: int, err: str) -> Result:
        clip = err.strip()[: self._k.detail_chars]
        if code == 0:
            return ok()
        return (unavailable if code == _SSH_LOST else failed)(clip or f"exit code {code}")

    def _piped(self, line: str, work: Callable[[P.Popen], object], timeout: int) -> P.PipedEnd:
        return P.run_piped(
            list(self.command(line)), work, timeout=timeout, stderr_bytes=self._k.detail_chars
        )

    def _ended(self, end: P.PipedEnd, timeout: int) -> Result:
        if end.error is not None:
            return unavailable(f"ssh could not run: {end.error}")
        if end.answer is not None:
            return end.answer
        if end.code is None or end.code < 0:
            return unavailable(f"{self.target} did not finish in {timeout}s")
        return self._judge(end.code, end.stderr.decode("utf-8", "replace"))

    def _call(self, line: str) -> tuple[Result, str]:
        got: list[bytes] = []

        def read(p: P.Popen) -> None:  # every answer is a line: keep a chunk, drain the rest
            p.stdin.close()  # type: ignore[union-attr]
            got.append(p.stdout.read(_CHUNK))  # type: ignore[union-attr]
            while p.stdout.read(_CHUNK):  # type: ignore[union-attr]
                pass

        t = self._k.ssh_timeout_s
        r = self._ended(self._piped(line, read, t), t)
        return r, b"".join(got).decode("utf-8", "replace")

    def start(self, lane: str, argv: Sequence[str]) -> Result:
        d = self._dir(lane)
        body = f"{shlex.join(argv)}; echo $? > lane.rc"
        line = (
            '[ -n "$(ps -o lstart= -p $$ 2>/dev/null)" ] || '
            "{ echo 'the remote ps has no lstart' >&2; exit 3; }\n"
            f"mkdir -p {d} && cd {d} || exit 1\n"
            f"if {_ALIVE}; then echo 'the lane is already running' >&2; exit 3; fi\n"
            "rm -f lane.rc lane.stopped lane.stamp\n"
            f"nohup setsid sh -c {shlex.quote(body)} >lane.log 2>&1 </dev/null &\n"
            f"echo $! > lane.pid\n{_STAMP} > lane.stamp || :"
        )
        return self._call(line)[0]

    def signal(self, lane: str, sig: str = "TERM") -> Result:
        if sig not in _SIGNALS:
            raise RemoteError(f"signal {sig!r} is not one of {', '.join(_SIGNALS)}")
        d = self._dir(lane)
        line = f'cd {d} && {_ALIVE} && : > lane.stopped && kill -s {sig} -- "-$(cat lane.pid)"'
        return self._call(line)[0]

    def observe(self, lane: str) -> LaneStatus | Result:
        r, out = self._call(
            f"cd {self._dir(lane)} 2>/dev/null || {{ echo absent; exit 0; }}\n{_OBSERVE}"
        )
        if not r.ok:
            return r
        word, _, rest = out.strip().partition(" ")
        if word == "running" and rest.isdigit():
            return LaneStatus("running", pid=int(rest))
        if word == "ended" and rest.isdigit():
            return LaneStatus("exited" if rest == "0" else "failed", exit_code=int(rest))
        if word == "stopped":
            return LaneStatus("stopped")
        if word == "gone":
            return LaneStatus("failed", detail="the process is gone and left no exit code")
        return failed("no such lane" if word == "absent" else "an unreadable answer")

    def copy_in(self, lane: str, local: Path, name: str) -> Result:
        line = f"mkdir -p {self._dir(lane)} && cat > {self._file(lane, name)}"

        def feed(p: P.Popen) -> None:
            with contextlib.suppress(BrokenPipeError), open(local, "rb") as fh:
                while chunk := fh.read(_CHUNK):
                    p.stdin.write(chunk)  # type: ignore[union-attr]
                p.stdin.close()  # type: ignore[union-attr]

        t = self._k.copy_timeout_s
        return self._ended(self._piped(line, feed, t), t)

    def copy_out(self, lane: str, name: str, local: Path, max_bytes: int) -> Result:
        """Stream a file back, cut at ``max_bytes``: the remote's word on size is not believed.
        Nothing is left at ``local`` unless the whole file came."""
        local = Path(local)
        part = local.with_name(f"{local.name}.part")

        def drain(p: P.Popen) -> Result | None:
            p.stdin.close()  # type: ignore[union-attr]
            total = 0
            with open(part, "wb") as fh:
                while chunk := p.stdout.read(_CHUNK):  # type: ignore[union-attr]
                    total += len(chunk)
                    if total > max_bytes:
                        return failed(f"the file is over the {max_bytes} byte cap")
                    fh.write(chunk)
            return None

        t = self._k.copy_timeout_s
        r = self._ended(self._piped(f"cat {self._file(lane, name)}", drain, t), t)
        if r.ok:
            part.replace(local)
        else:
            part.unlink(missing_ok=True)
        return r

    def stdio_argv(self, lane: str, argv: Sequence[str]) -> tuple[str, ...]:
        """The ssh argv whose stdin and stdout ARE the lane's: ACP over SSH stdio (Q8)."""
        d = self._dir(lane)
        return self.command(f"mkdir -p {d} && cd {d} && exec {shlex.join(argv)}")

    def cleanup(self, lane: str) -> Result:
        d = self._dir(lane)
        line = (
            f"( cd {d} 2>/dev/null || exit 0\n"
            f"if {_ALIVE}; then\n"
            'kill -s KILL -- "-$(cat lane.pid)"; fi )\n'
            f"rm -rf -- {d}"
        )
        return self._call(line)[0]
