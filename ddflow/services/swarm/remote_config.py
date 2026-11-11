"""The swappable knobs and the one name guard the ssh transport reads, apart from the verbs.

`remote` (the host's one-writer verbs) and `remote_ssh` (`SshTransport`) both read `Knobs`,
`DEFAULT`, `RemoteError` and `_safe`; they live here so `remote_ssh` reads them WITHOUT
importing `remote`. That keeps `remote`'s re-export of `SshTransport` a one-way edge, so the
split stays a DAG (r-small-files) and never a latent import cycle.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass

from ...config import Config
from ...core.outcome import FAIL


class RemoteError(RuntimeError):
    exit_code = FAIL


_FILE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")


@dataclass(frozen=True)
class Knobs:
    """SW-remote-config feeds these from ``[swarm].remote_*``; ``from_config`` is the one reader."""

    exclude: tuple[str, ...] = (".ddflow", ".env", "**/.env")
    bundle_max_bytes: int = 104857600
    return_max_bytes: int = 52428800
    return_max_commits: int = 200
    git_timeout_s: int = 120
    scan_max_blob_bytes: int = 1048576
    scan_batch_files: int = 64  # at most this many blobs of scan_max_blob_bytes in memory
    branch_prefix: str = "swarm/remote/"
    seed_name: str = "SEED.md"
    proposals_max: int = 5
    proposal_chars: int = 400
    proposals_max_bytes: int = 65536
    ssh_argv: tuple[str, ...] = ("ssh",)
    ssh_timeout_s: int = 60
    ssh_connect_timeout_s: int = 15
    copy_timeout_s: int = 600
    lanes_root: str = "ddflow-lanes"  # under the remote home
    detail_chars: int = 200

    @classmethod
    def from_config(cls, cfg: Config) -> Knobs:
        """Read the ``[swarm] remote_*`` knobs; one per field, so a new field needs a knob."""
        sw = cfg.swarm
        kw = {
            f.name: getattr(sw, "remote_" + f.name) for f in dataclasses.fields(cls)
        }
        # a config list one is the tuple field (a frozen knob), never a list object in Knobs
        for name in ("exclude", "ssh_argv"):
            kw[name] = tuple(kw[name])
        return cls(**kw)


DEFAULT = Knobs()


def _safe(s: str) -> str:
    """``s``, if one harmless path and ref component. Never rewritten: that could give two
    lanes one ref."""
    if not _FILE.fullmatch(s) or ".." in s or s.endswith((".lock", ".")):
        raise RemoteError(f"{s!r} is not one plain name")
    return s
