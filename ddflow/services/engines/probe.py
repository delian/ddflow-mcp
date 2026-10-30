"""E8 probe: ask an installed agent what it is, with declared read-only argv only.

No shell, no network flag, no model call: every argument must be on an allowlist of
read-only switches, the child gets a scrubbed environment (no API keys), a timeout and an
output cap. The result is a fact (``verified`` from a probe, or ``unavailable``).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

from .render_argv import SHELLS
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


def parse(argvs: object, limits: Limits = DEFAULT, allowed: frozenset[str] = SAFE_ARGS):
    """A list of argv lists, each ``[program, safe-switch...]``."""
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
        if argv[0].rsplit("/", 1)[-1] in SHELLS:
            raise EngineError(f"probe[{i}]: {argv[0]!r} is a shell")
        bad = [a for a in argv[1:] if a not in allowed]
        if bad:
            raise EngineError(
                f"probe[{i}]: {bad[0]!r} is not a declared read-only switch {sorted(allowed)}"
            )
        out.append(tuple(argv))
    return tuple(out)


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
            p = subprocess.run(
                [exe, *argv[1:]],
                capture_output=True,
                stdin=subprocess.DEVNULL,
                env=env,
                timeout=limits.probe_timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            facts.append({**fact, "status": "unavailable", "reason": "timeout"})
            continue
        except OSError as exc:
            facts.append(
                {**fact, "status": "unavailable", "reason": f"cannot run: {exc.strerror or exc}"}
            )
            continue
        out = (p.stdout + p.stderr)[: limits.probe_output_bytes].decode("utf-8", "replace")
        fact.update(status="verified", exit_code=p.returncode, output=out)
        m = _VERSION.search(out)
        if m:
            fact["tool_version"] = m.group(0)
        facts.append(fact)
    return facts
