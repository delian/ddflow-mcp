"""Long-running processes started for an item, and whether they are still running.

Three facts decide what an agent should do about a job, and none of them is in the log
by itself: is the process alive, is it the SAME process (pids are reused), and if it is
gone, how did it end. This module computes them; the log records only that a job was
started and, when someone says so, that it ended.

Launching goes through a shell that detaches the job into its own session and exits at
once. Two reasons. A child of the MCP server would become a zombie when it finished --
and a zombie answers `kill(pid, 0)` as though it were alive, so a finished job would
read as running forever. And a job in the server's process group dies with the server,
which is exactly the event (a restarted remote-control service) that a multi-hour run
must survive.
"""

from __future__ import annotations

import os
import re
import socket
from dataclasses import dataclass
from pathlib import Path

from ..core.model import Job
from ..infra import proc as P

#: The line the launch wrapper appends when the job's command exits, so a job that
#: nobody watched still says how it ended.
EXIT_MARK = "ddflow-job-exit:"
_EXIT_RE = re.compile(rf"^{re.escape(EXIT_MARK)}\s*(-?\d+)\s*$", re.M)

#: The launcher. `$0` is the wrapped script, `$1` the log. `setsid` where it exists (a
#: new session: no terminal hang-up and no process-group kill reaches it), `nohup` where
#: not.
_LAUNCH = (
    "if command -v setsid >/dev/null 2>&1; then S=setsid; else S=nohup; fi; "
    '$S /bin/sh -c "$0" > "$1" 2>&1 < /dev/null & echo $!'
)


def _wrapped(command: str) -> str:
    """The command in a SUBSHELL, then its exit code. Without the subshell a command
    that says `exit 7` leaves before the code is written, and reads as killed."""
    # The marker starts on a line of its own: after output with no trailing newline,
    # `echo` glued it mid-line, `logged_exit` (anchored at ^) missed it, and a clean
    # exit read as "killed" (cross-family critic).
    return f'(\n{command}\n)\nrc=$?; printf "\\n{EXIT_MARK} %s\\n" "$rc"; exit $rc\n'


#: `starttime` (field 22 of /proc/<pid>/stat) counted from field 3, the first after the
#: parenthesised command name.
_STARTTIME = 19


def host() -> str:
    return socket.gethostname()


def proc_start(pid: int) -> str:
    """The kernel's start time for `pid` (Linux `/proc/<pid>/stat` field 22), or ""."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return ""
    # The command name (field 2) is parenthesised and may contain spaces or ')'.
    rest = raw.rsplit(")", 1)[-1].split()
    return rest[_STARTTIME] if len(rest) > _STARTTIME else ""


def launch(command: str, cwd: Path, log: Path) -> int:
    """Start `command` detached, output to `log`. Returns the job's pid."""
    log.parent.mkdir(parents=True, exist_ok=True)
    r = P.run(
        ["/bin/sh", "-c", _LAUNCH, _wrapped(command), str(log)],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
    )
    pid = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    if r.returncode != 0 or not pid.isdigit():
        raise RuntimeError(f"could not launch the job: {(r.stderr or r.stdout).strip()[:300]}")
    return int(pid)


@dataclass
class Status:
    #: running | exited | gone | ended | elsewhere
    state: str
    detail: str
    exit_code: int | None = None


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie still answers kill(0): its entry exists until someone reaps it.
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[-1].split()[0]
    except OSError:
        return True
    return state != "Z"


def logged_exit(log: str) -> int | None:
    """The exit code the launch wrapper wrote, if the job got that far."""
    try:
        tail = Path(log).read_bytes()[-4096:].decode("utf-8", "replace")
    except OSError:
        return None
    found = _EXIT_RE.findall(tail)
    return int(found[-1]) if found else None


def status(job: Job) -> Status:
    """What an agent needs to know about a job RIGHT NOW."""
    if job.ended_at:
        return Status(
            "ended",
            f"ended, exit {job.exit_code}" if job.exit_code is not None else "ended",
            job.exit_code,
        )
    if job.host and job.host != host():
        return Status("elsewhere", f"on host {job.host}; cannot be checked from here")
    if job.pid and alive(job.pid):
        if job.proc_start and proc_start(job.pid) not in ("", job.proc_start):
            code = logged_exit(job.log)
            return Status(
                "exited" if code is not None else "gone",
                f"pid {job.pid} now belongs to a DIFFERENT process; the job is not running",
                code,
            )
        return Status("running", f"pid {job.pid} running")
    code = logged_exit(job.log)
    if code is not None:
        return Status("exited", f"exited {code} (from its log); `job end` records it", code)
    return Status("gone", f"pid {job.pid} is gone and its log records no exit: it was killed")
