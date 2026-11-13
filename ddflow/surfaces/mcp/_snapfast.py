"""Answer the handshake from the cached interface snapshot (SU-init-fast-hook).

`served` returns the snapshot a handshake may be answered from: written by this ddflow version
for this connection's tier and interface digest, not older than `[mcp].init_snapshot_max_age_s`.
After a handshake that was rendered live the server writes the snapshot (`write_after`); a
write that fails costs nothing (the handshake is already answered). The snapshot holds the
STABLE instruction text only: the volatile upgrade notice is said on the first tool result.
"""

from __future__ import annotations

from typing import Any

from ...config import Config
from ...core import clock
from ...core.version import running
from ...mcpshim import snapshot as S
from .. import ifacedigest as D
from .. import ifacesnap


def _max_age(repo: Any) -> float:
    try:
        return float(Config.load(repo).mcp.init_snapshot_max_age_s)
    except Exception:
        return 0.0  # a project whose config cannot be read is served live


def served(server: Any) -> dict[str, Any] | None:
    """The fresh snapshot `server` may answer a handshake from, or None (render live)."""
    try:
        age = _max_age(server.repo)
        if age <= 0:
            return None
        data = S.read(server.repo)
        if data is None or data.get("tier") != server.tier:
            return None
        if not S.fresh(data, running()):
            return None
        written = data.get("written_at")
        if not isinstance(written, (int, float)) or isinstance(written, bool):
            return None
        if not 0 <= clock.now_utc().timestamp() - written <= age:
            return None
        if data["digest"] != D.digest(D.live_snapshot(server)):
            return None
        return data
    except Exception:
        return None


def write_after(server: Any, instructions: str) -> bool:
    """Write the snapshot for `server`'s tier with `instructions`; False when it could not, or
    when the project is not adopted: a handshake is a read and must leave nothing behind in a
    repository ddflow was only connected to."""
    try:
        if not (server.repo / ".ddflow" / "config.toml").is_file():
            return False
        return ifacesnap.write(server, instructions=instructions)
    except Exception:
        return False
