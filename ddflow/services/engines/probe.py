"""E8 probe: ask an installed agent what it is, with declared read-only argv only.

No shell, no network flag, no model call: every argument must be on an allowlist of
read-only switches, the child gets a scrubbed environment (no API keys), a timeout and an
output cap. The result is a fact (``verified`` from a probe, or ``unavailable``).
"""

from __future__ import annotations

import os
import re
import select
import shutil
import time

from ...infra import proc
from .render_argv import is_shell
from .safety import DEFAULT, EngineError, Limits

SAFE_ARGS = frozenset(
    {
        "--version",
        "-V",
        "version",
        "--help",
        "-h",
        "--dump-config",
        "--dump-default-config",
        "--dump-config-schema",
    }
)
MIN_ARGV, MAX_ARGV = 2, 4  # program + 1..3 switches
ENV_KEEP = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
_VERSION = re.compile(r"\d+(?:\.\d+){1,3}(?:-[0-9A-Za-z.]+)?")


def parse(
    argvs: object,
    limits: Limits = DEFAULT,
    allowed: frozenset[str] = SAFE_ARGS,
    allow_paths: bool = False,
):
    """A list of argv lists, each ``[program, safe-switch...]``. ``program`` is a bare name found on
    PATH unless ``allow_paths`` (a path can name a script, which is arbitrary code)."""
    if not isinstance(argvs, list) or len(argvs) > limits.max_rules:
        raise EngineError("probe: a short list of argv lists")
    out = []
    for i, argv in enumerate(argvs):
        if (
            not isinstance(argv, list)
            or not MIN_ARGV <= len(argv) <= MAX_ARGV
            or not all(isinstance(a, str) and a and "\0" not in a for a in argv)
        ):
            raise EngineError(f"probe[{i}]: [program, switch...] of 2 to 4 strings")
        if is_shell(argv[0]) or (not allow_paths and ("/" in argv[0] or "\\" in argv[0])):
            raise EngineError(
                f"probe[{i}]: {argv[0]!r} is a shell or a path (programs are bare names on PATH)"
            )
        bad = [a for a in argv[1:] if a not in allowed]
        if bad:
            raise EngineError(
                f"probe[{i}]: {bad[0]!r} is not a declared read-only switch {sorted(allowed)}"
            )
        out.append(tuple(argv))
    return tuple(out)


READ_CHUNK = 65536
TICK_S = 0.1  #: how often the wait wakes to check the deadline


def _run_capped(cmd: list[str], env: dict[str, str], limits: Limits) -> tuple[int, str] | None:
    """Run ``cmd`` reading stdout and stderr in chunks (their order is as it arrives). Past ``probe_output_bytes``
    or ``probe_timeout_s`` the child's whole process group is killed, so a huge writer cannot
    fill memory. None on timeout; the exit code is the child's, or -9 after a cap kill."""
    if os.name != "posix":  # select() on pipes is POSIX-only
        raise OSError("probe needs a POSIX host")
    err_r, err_w = os.pipe()
    try:
        child = proc.spawn_stdio(cmd, env=env, stderr=err_w)
    finally:
        os.close(err_w)
    child.stdin.close()  # EOF at once: a probe never reads input
    open_fds = [child.stdout.fileno(), err_r]
    chunks: list[bytes] = []
    total = 0
    timed_out = False
    deadline = time.monotonic() + limits.probe_timeout_s
    try:
        while open_fds and total < limits.probe_output_bytes:
            left = deadline - time.monotonic()
            if left <= 0:
                timed_out = True
                break
            for fd in select.select(open_fds, [], [], min(left, TICK_S))[0]:
                if total >= limits.probe_output_bytes:
                    break
                data = os.read(fd, READ_CHUNK)
                if not data:
                    open_fds.remove(fd)  # EOF: the child closed this stream
                    continue
                chunks.append(data[: limits.probe_output_bytes - total])
                total += len(data)
    finally:
        os.close(err_r)
        child.stdout.close()
    if timed_out or total >= limits.probe_output_bytes:
        proc.stop_group(child, reap_s=2)
    else:
        try:
            child.wait(timeout=max(deadline - time.monotonic(), 0.01))
        except Exception:
            proc.stop_group(child, reap_s=2)
            return None
    if timed_out:
        return None
    return (child.returncode if child.returncode is not None else -9), b"".join(chunks).decode(
        "utf-8", "replace"
    )


def run(argvs, *, probe_id: str, now: str, limits: Limits = DEFAULT) -> list[dict]:
    """Run each argv once; one fact per argv."""
    env = {k: os.environ[k] for k in ENV_KEEP if k in os.environ}
    facts = []
    for argv in argvs:
        fact: dict = {"argv": list(argv), "source": f"probe:{probe_id}", "retrieved": now}
        exe = shutil.which(argv[0])
        if exe is None:
            facts.append({**fact, "status": "unavailable", "reason": "not installed"})
            continue
        try:
            res = _run_capped([exe, *argv[1:]], env, limits)
        except OSError as exc:
            facts.append(
                {**fact, "status": "unavailable", "reason": f"cannot run: {exc.strerror or exc}"}
            )
            continue
        if res is None:
            facts.append({**fact, "status": "unavailable", "reason": "timeout"})
            continue
        code, out = res
        fact.update(status="verified", exit_code=code, output=out)
        m = _VERSION.search(out[: limits.max_match_chars])
        if m:
            fact["tool_version"] = m.group(0)
        facts.append(fact)
    return facts
