"""The default `Transport`: one lane as one local subprocess in its own directory.

The local mirror of the ssh remote (`ddflow.services.swarm.remote.SshTransport`), with the
same private directory per lane and the same ``lane.log`` for the lane's output. The child is
spawned directly from the argv list (no shell), leads a session of its own so the whole group
is signalled, and stays held in memory here, so liveness IS the held child -- a recycled pid
can never be signalled. Files cross the boundary as local copies (`Fs`), output comes back as
the lane's ``lane.log`` through `copy_out`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import fs, fsio
from . import proc as P
from .lanes import LaneStatus, Result, failed, ok
from .proc_server import kill_group, stop_group

#: The lane's captured output (stdout and stderr merged), read back with `copy_out`.
LOG = "lane.log"
#: A lane name is one plain file name: it names the lane's directory.
_FILE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")
#: signal name -> number; the vocabulary the remote bundle allowed (`_SIGNALS`). TERM, INT and
#: KILL end the lane; HUP is a hangup, which a lane may trap and survive, so it alone is not a stop.
_SIGNALS = {"TERM": 15, "INT": 2, "HUP": 1, "KILL": 9}
_STOP = frozenset({"TERM", "INT", "KILL"})
#: copy_out reads this much at a time, so the cap bounds the read, not just the copy.
_CHUNK = 65536


@dataclass
class _Run:
    popen: Any
    dir: Path
    stopped: bool = False


def _safe(lane: str) -> str:
    """``lane``, if one plain name; a directory separator or a git lock-shaped name would let
    one lane's name become a path, so it is refused."""
    if not _FILE.fullmatch(lane) or ".." in lane or lane.endswith((".lock", ".")):
        raise ValueError(f"lane {lane!r} is not one plain name")
    return lane


class LocalTransport:
    """`Transport` for the local machine: `start`/`observe`/`signal` hold the real child in
    memory; `copy_in`/`copy_out`/`cleanup` reach the lane's directory. One instance serves
    any number of lanes."""

    name = "local"

    def __init__(self, root: Path | str | None = None) -> None:
        self.target = "local"
        self._root: Path | None = None if root is None else Path(root)
        self._runs: dict[str, _Run] = {}

    def _root_dir(self) -> Path:
        if self._root is None:
            self._root = fsio.unused_dir("ddflow-transport-")
        return self._root

    def _dir(self, lane: str) -> Path:
        return self._root_dir() / _safe(lane)

    def _run(self, lane: str) -> _Run | None:
        return self._runs.get(_safe(lane))

    def start(self, lane: str, argv: Any) -> Result:
        run = self._run(lane)
        if run is not None and run.popen.poll() is None:
            return failed(f"lane {lane!r} is already running")
        d = self._dir(lane)
        d.mkdir(parents=True, exist_ok=True)
        log = d / LOG
        side = d / f"{LOG}.next"  # fresh: the previous log stays put until the child exists
        try:
            fh = side.open("wb")
        except OSError as e:
            return failed(f"cannot open the lane log: {e}")
        try:
            # one open file description for both streams: fd 1 and 2 share the offset (`2>&1`);
            # the child runs in the lane directory, so a copied file is where a relative argv sees it.
            p = P.popen(list(argv), cwd=d, stdout=fh, stderr=fh, start_new_session=True)
        except (OSError, ValueError) as e:
            fh.close()
            side.unlink(missing_ok=True)
            return failed(f"cannot start {argv[0] if argv else 'a process'}: {e}")
        fh.close()
        try:  # only a start that succeeded becomes the lane log; a failed one kept the old file
            fs.DEFAULT.replace_path(side, log)
        except OSError as e:
            kill_group(p)
            side.unlink(missing_ok=True)
            return failed(f"cannot put the lane log in place: {e}")
        self._runs[_safe(lane)] = _Run(p, d)
        return ok()

    def signal(self, lane: str, sig: str = "TERM") -> Result:
        if sig not in _SIGNALS:
            return failed(f"signal {sig!r} is not one of {', '.join(_SIGNALS)}")
        run = self._run(lane)
        if run is None:
            return failed(f"no such lane {lane!r}")
        if run.popen.poll() is not None:
            return failed("the lane has ended")
        if sig in _STOP:
            run.stopped = True
        kill_group(run.popen, _SIGNALS[sig])
        return ok(f"{sig} sent to the process group")

    def observe(self, lane: str) -> LaneStatus | Result:
        run = self._run(lane)
        if run is None:
            return failed(f"no such lane {lane!r}")
        code = run.popen.poll()
        if code is None:
            return LaneStatus("running", pid=run.popen.pid)
        # stopped = we asked to stop AND the child died BY a signal (a trapped term that then
        # exits nonzero failed on its own, so it is not silenced as a stop).
        state = "stopped" if run.stopped and code < 0 else "exited" if code == 0 else "failed"
        return LaneStatus(state, pid=run.popen.pid, exit_code=code)

    def copy_in(self, lane: str, local: Path, name: str) -> Result:
        src = Path(local)
        if not src.is_file():
            return failed(f"{src} is not a file to copy in")
        d = self._dir(lane)
        d.mkdir(parents=True, exist_ok=True)
        try:
            fs.DEFAULT.copy_file(src, d / _safe(name))
        except OSError as e:
            return failed(f"cannot copy {src} into lane {lane!r}: {e}")
        return ok()

    def copy_out(self, lane: str, name: str, local: Path, max_bytes: int) -> Result:
        src = self._dir(lane) / _safe(name)
        if not src.is_file():
            return failed(f"no {name!r} in lane {lane!r}")
        try:
            data = bytearray()
            with src.open("rb") as fh:
                while chunk := fh.read(_CHUNK):
                    if len(data) + len(chunk) > max_bytes:
                        return failed(f"the file is over the {max_bytes} byte cap")
                    data += chunk
        except OSError as e:
            return failed(f"cannot read {name!r} of lane {lane!r}: {e}")
        try:
            Path(local).write_bytes(bytes(data))
        except OSError as e:
            return failed(f"cannot write {name!r} to {local}: {e}")
        return ok()

    def cleanup(self, lane: str) -> Result:
        run = self._run(lane)
        if run is not None:
            if run.popen.poll() is None:
                stop_group(run.popen)
            self._runs.pop(_safe(lane), None)
        fs.DEFAULT.rmtree(self._dir(lane))  # no-op when the directory never existed
        return ok()
