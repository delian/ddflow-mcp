"""Match a stored MCP config entry to a companion by its launch command.

Split out of ``ddflow/services/companions.py``: ``launches_as`` decides whether an
agent's stored server entry starts this companion — strict about the command and the
companion's arguments, strict about which flags take a value, and version-tag-blind
for an npm launcher. Reading what IS registered (``.registry``) builds on this.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..mcpconfig import _launch_of
from .model import Companion

#: An npm package spec with a version suffix: `pkg@latest`, `@scope/pkg@1.2.3`, `pkg@^2`.
#: The name part is npm's own charset (no `:` or `/` beyond the scope), and the rule runs
#: only for an npm launcher (`NPM_LAUNCHERS`): run on every argument, it read the host in
#: `postgresql://u:p@1db.example` as a version and made two databases one server.
_NPM_TAG = re.compile(
    r"^((?:@[a-z0-9][\w.-]*/)?[a-z0-9][\w.-]*)@(?:latest|next|[\^~]?v?\d[\w.+-]*)$"
)

#: Launchers whose arguments name an npm package, where a version tag is not identity.
NPM_LAUNCHERS = frozenset({"npx", "npm", "pnpm", "pnpx", "bunx", "yarn"})


def _norm_args(cmd: str, args: list[str]) -> list[str]:
    if Path(cmd).name not in NPM_LAUNCHERS:
        return list(args)
    return [m.group(1) if (m := _NPM_TAG.match(a)) else a for a in args]


def launches_as(c: Companion, entry: object) -> bool:
    """Does this stored server entry launch companion ``c``?

    How strict "the same launch" is, decided once:

    * the same COMMAND, compared by basename (`/usr/local/bin/npx` is `npx`);
    * the companion's ARGUMENTS appear in the entry IN ORDER, with only flags (and the
      value of a flag known to take one, `VALUE_FLAGS`) between them -- `docker run --rm -i -e TOKEN <image>` is still that
      image, `docker run <other-image> <image>` is not -- and anything after them;
    * an npm version tag is ignored on both sides (`@upstash/context7-mcp@latest`),
      for an npm launcher only -- elsewhere `x@1.2` is not a package and not a tag;
    * a companion that declares no arguments matches only an entry with none, or every
      server started by the same launcher would count.

    Everything else is a different server: another image or package (a package whose
    name merely starts with the companion's is not it), another launcher, reordered
    arguments. Environment is ignored -- it configures a server, it does not pick one.
    """
    if not c.is_mcp or not c.command:
        return False
    launch = _launch_of(entry)
    if launch is None:
        return False
    cmd, args = launch
    if Path(cmd).name != Path(c.command).name:
        return False
    want = _norm_args(c.command, c.args)
    have = _norm_args(cmd, args)
    if not want:
        return not have
    return _in_order_with_flags(want, have, VALUE_FLAGS.get(Path(cmd).name, frozenset()))


#: Flags known to take their value as the NEXT argument, per launcher. Whether `-x v`
#: is a flag and its value or a boolean flag and a positional cannot be told from the
#: tokens, and guessing "value" let `docker run -i <other-image> <image>` pass as the
#: companion. So only these consume a following token; any other flag is taken as
#: boolean, and a value written `--flag=value` needs no entry here.
_DOCKER_VALUE_FLAGS = frozenset(
    {"-a", "--attach", "--add-host", "--annotation", "--blkio-weight", "--blkio-weight-device", "-c", "--cap-add", "--cap-drop",
     "--cgroup-parent", "--cgroupns", "--cidfile", "--cpu-count", "--cpu-percent",
     "--cpu-period", "--cpu-quota", "--cpu-rt-period", "--cpu-rt-runtime", "--cpu-shares",
     "--cpus", "--cpuset-cpus", "--cpuset-mems", "--detach-keys",
     "--device-read-bps", "--device-read-iops", "--device-write-bps", "--device-write-iops",
     "--device", "--device-cgroup-rule", "--dns", "--dns-opt", "--dns-option", "--dns-search",
     "--domainname", "-e", "--entrypoint", "--env", "--env-file", "--expose", "--gpus",
     "--group-add", "-h", "--health-cmd", "--health-interval", "--health-retries",
     "--health-start-interval", "--health-start-period", "--health-timeout", "--hostname",
     "--ip", "--ip6", "--ipc", "--kernel-memory", "--link-local-ip",
     "--isolation", "-l", "--label", "--label-file", "--link", "--log-driver", "--log-opt",
     "-m", "--mac-address", "--memory", "--memory-reservation", "--memory-swap",
     "--memory-swappiness", "--network-alias", "--pids-limit", "--mount", "--name", "--net",
     "--network", "--oom-score-adj", "-p", "--pid", "--platform", "--publish", "--pull",
     "--restart", "--runtime", "--security-opt", "--shm-size", "--stop-signal",
     "--stop-timeout", "--storage-opt", "--sysctl", "--tmpfs", "-u", "--ulimit", "--user",
     "--userns", "--uts", "-v", "--volume", "--volume-driver", "--volumes-from", "-w", "--workdir"}
)  # fmt: skip
VALUE_FLAGS: dict[str, frozenset[str]] = {
    "docker": _DOCKER_VALUE_FLAGS,
    "podman": _DOCKER_VALUE_FLAGS,
    # Not npx: its `-p <pkg>` value IS the package that names the server, so consuming it
    # as a flag's value hid the one token the match needs. Taken as a flag, `-p` is
    # skipped and the package is compared like any other argument.
}


def _in_order_with_flags(want: list[str], have: list[str], value_flags: frozenset[str]) -> bool:
    """``want`` appears in ``have`` in order, with only FLAGS between its items.

    Between two of the companion's arguments the entry may add a flag (`-i`, `--x=y`),
    and a flag in ``value_flags`` may bring the one value after it (`-e TOKEN`). Any
    other bare positional there is another package or image, and the companion's own
    name after it is only that server's argument (`npx -y server-github
    server-filesystem` launches github). Once every item of ``want`` is matched, the
    rest is the server's own configuration and is allowed.
    """
    i, takes_value = 0, False
    for h in have:
        if i == len(want):
            return True
        if takes_value:
            takes_value = False
        elif h == want[i]:
            i += 1
        elif h.startswith("-"):
            takes_value = h in value_flags
        else:
            return False
    return i == len(want)
