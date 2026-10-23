"""The cached interface snapshot the shim answers from (SU-init-fast).

The worker WILL write `.ddflow/local/mcp-interface.json` when it starts and whenever the interface
digest changes (`ddflow.surfaces.ifacesnap`; the call is SU-init-fast-hook, nothing calls it
yet); the shim (SU-shim) will read it and serves `initialize`,
`server/discover`, `tools/list` and `prompts/list` without waking the worker, so a connection
costs milliseconds instead of the seconds a cold worker needs.

The file holds exactly what those four replies are made of: protocol versions, capabilities,
server identity, the instructions text, and the tool and prompt lists of one tier, plus the
interface digest, the ddflow version that wrote it and the tier. A snapshot is `fresh` only for
the version that wrote it; any problem (missing, unparseable, wrong form, a field of the wrong
type) reads as no snapshot, and the caller falls back to the worker.

The reply builders mirror `surfaces.mcp_protocol.initialize_result` / `discover` (this module
cannot import them: the shim stays stdlib-only); `tests/test_mcp_soft_snapshot.py` pins that they
agree for every served version, and `ifacesnap.drift` checks it against the live server.

Stdlib only: the shim imports this module. The modern (2026-07-28) wrapping of a result
(`resultType`, `_meta`, cache hints) is the shim's job, as it is `Server`'s for the live ones.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

FORM = 1
#: Under the git-ignored machine-local directory, with the rest of the derived state.
RELATIVE = Path(".ddflow") / "local" / "mcp-interface.json"
_STRINGS = ("version", "digest", "tier", "instructions")
_LISTS = ("protocols", "modern_protocols", "tools", "prompts")


def path(repo: Path | str) -> Path:
    return Path(repo) / RELATIVE


def _field_problems(data: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if type(data.get("form")) is not int or data["form"] != FORM:
        out.append(f"form {data.get('form')!r}, expected {FORM}")
    for key in _STRINGS:
        if not isinstance(data.get(key), str) or (key != "instructions" and not data[key]):
            out.append(
                f"{key} must be a {'string' if key == 'instructions' else 'non-empty string'}"
            )
    for key in _LISTS:
        if not isinstance(data.get(key), list):
            out.append(f"{key} must be a list")
    if not isinstance(data.get("capabilities"), dict):
        out.append("capabilities must be an object")
    info = data.get("server_info")
    if not isinstance(info, dict) or not isinstance(info.get("name"), str):
        out.append("server_info must be an object with a name")
    return out


def problems(data: Any) -> list[str]:
    """Why `data` is not a usable snapshot; empty when it is."""
    if not isinstance(data, dict):
        return ["snapshot is not an object"]
    out = _field_problems(data)
    if isinstance(data.get("protocols"), list) and not data["protocols"]:
        out.append("protocols must name at least one version")
    for key in ("protocols", "modern_protocols"):
        if isinstance(data.get(key), list) and not all(isinstance(v, str) and v for v in data[key]):
            out.append(f"every entry of {key} must be a non-empty version string")
    for key in ("tools", "prompts"):
        items = data.get(key)
        if isinstance(items, list) and not all(
            isinstance(i, dict) and isinstance(i.get("name"), str) and i["name"] for i in items
        ):
            out.append(f"every entry of {key} needs a non-empty string name")
    return out


def read(repo: Path | str) -> dict[str, Any] | None:
    """The valid snapshot of `repo`, or None (missing, unreadable, malformed)."""
    try:
        data = json.loads(path(repo).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return data if not problems(data) else None


def fresh(data: dict[str, Any] | None, version: str) -> bool:
    """Was `data` written by ddflow `version`? Another version's interface may differ."""
    return data is not None and data.get("version") == version


def dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n"


def initialize_result(data: dict[str, Any], want: Any) -> dict[str, Any]:
    """The legacy `initialize` result: the version asked for when served, else the newest."""
    version = want if want in data["protocols"] else data["protocols"][0]
    return {
        "protocolVersion": version,
        "capabilities": copy.deepcopy(data["capabilities"]),
        "serverInfo": copy.deepcopy(data["server_info"]),
        "instructions": data["instructions"],
    }


def discover_result(data: dict[str, Any]) -> dict[str, Any]:
    """`server/discover`: the versions served (modern first), capabilities and instructions."""
    return {
        "supportedVersions": [*data["modern_protocols"], *data["protocols"]],
        "capabilities": copy.deepcopy(data["capabilities"]),
        "instructions": data["instructions"],
    }


def tools_list_result(data: dict[str, Any]) -> dict[str, Any]:
    return {"tools": copy.deepcopy(data["tools"])}


def prompts_list_result(data: dict[str, Any]) -> dict[str, Any]:
    return {"prompts": copy.deepcopy(data["prompts"])}
