"""Driver packs: user, project and company descriptor/model packs in layer order.

Extension beyond the shipped descriptors and models, per D-DRV-5 and D-DRV-Q5: the
single combined set loads from the D-config-layer-order layers -- packaged (the shipped
``ddflow/harnesses``), user (``~/.config/ddflow/harnesses.d``), project
(``.ddflow/harnesses.d``) and machine-local (``.ddflow/local/harnesses.d``) -- merged per
descriptor with later layers winning, and models overlay the shipped catalog the same
way (``models.toml`` under the user, project and local layers).

Security split (D-DRV-Q5): a descriptor from a cloned project may add detection, paths
and facts, but never a key that runs a process. ``[headless] argv`` and ``[probe]`` are
operator-only: they are read only from the operator layers (packaged, user, machine-local,
company enforced), so a cloned project that smuggles a launch command is refused at load,
not run. A company pack is data only and pinned by digest (the CM-packs pattern): a bare
URL without a digest is refused, a mismatched digest keeps the content out.

Schema versioned (D-DRV-5): each file carries ``schema = N``. A newer schema is
"unavailable: upgrade ddflow", never half-read; upgrade is the existing upgrade manifest's
job, not a reader guessing at an unknown shape. Unknown keys stay an error.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from ..adapters.env import DEFAULT as ENV
from ..adapters.http import UrllibHttpClient
from ..core.digest import content_digest
from . import harnessreg
from .harnessreg import Harness

#: A pack is a small set of TOML files; a larger body than this is not a descriptor pack
#: (bounded, never an unbounded fetch).
MAX_PACK_BYTES = 2_000_000

#: The schema this code reads. A file that says a newer one is "unavailable: upgrade
#: ddflow", never half-read.
CURRENT_SCHEMA = 2

#: Keys that run a process, operator-only (D-DRV-Q5): ``[headless] argv`` is the launch
#: command template (E4), ``[probe]`` the read-only probe argv (E8). A non-operator layer
#: (a cloned project, a company data pack) may never set one.
OPERATOR_ONLY = (("headless", "argv"), ("probe",))


class PackError(ValueError):
    """A descriptor pack is refused: wrong layer for a key, a bare URL, a bad schema."""


def user_dir() -> Path:
    """The user ddflow directory: ``$XDG_CONFIG_HOME/ddflow``, else ``~/.config/ddflow``."""
    base = ENV.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "ddflow"


def descriptor_dirs(
    root: Path | str, user_home: Path | str | None = None
) -> tuple[tuple[str, Path], ...]:
    """Each descriptor layer's directory, weakest first: packaged, user, project, local."""
    ud = Path(user_home) if user_home is not None else user_dir()
    root = Path(root)
    return (
        ("packaged", harnessreg.HARNESS_DIR),
        ("user", ud / "harnesses.d"),
        ("project", root / ".ddflow" / "harnesses.d"),
        ("local", root / ".ddflow" / "local" / "harnesses.d"),
    )


def model_overlay_paths(root: Path | str, user_home: Path | str | None = None) -> tuple[Path, ...]:
    """The model overlay files, weakest first: user, project, then local ``models.toml``.

    Each is the shape ``modelreg.LOCAL_FILE``: ``[family] [model] [route] [class]`` tables
    that overlay the shipped ``ddflow/models`` fact-by-fact (``modelreg._merge_row``), so a
    layer corrects one number without restating the row.
    """
    ud = Path(user_home) if user_home is not None else user_dir()
    root = Path(root)
    return (
        ud / "models.toml",
        root / ".ddflow" / "models.toml",
        root / ".ddflow" / "local" / "models.toml",
    )


def schema_of(text: str) -> int | None:
    """The ``schema`` a file declares, or ``None`` when it carries none (the shipped,
    schema-less shape this code reads as its own)."""
    data = tomllib.loads(text)
    s = data.get("schema")
    if s is None:
        return None
    if type(s) is not int:
        raise PackError(f"schema: expected int, got {type(s).__name__}")
    return s


def require_known_schema(text: str, supported: int = CURRENT_SCHEMA) -> None:
    """Refuse a file newer than this code: "unavailable: upgrade ddflow", never half-read."""
    s = schema_of(text)
    if s is not None and s > supported:
        raise PackError(
            f"schema {s} is newer than this ddflow reads ({supported}): unavailable: upgrade ddflow"
        )


def operator_only_violations(raw: Any) -> list[str]:
    """The process-running keys ``raw`` sets, in the order they would be refused. Empty when
    the table is not a descriptor table or sets none."""
    if not isinstance(raw, dict):
        return []
    out: list[str] = []
    headless = raw.get("headless")
    if isinstance(headless, dict) and "argv" in headless:
        out.append("headless.argv")
    if "probe" in raw:
        out.append("probe")
    return out


def _layer_descriptors(directory: Path, layer: str) -> list[Harness]:
    """Parse one layer's descriptors. The project layer (a clone) may not set an
    operator-only key; the check reads the raw table so an explicit empty ``argv`` is a
    refusal, not a default."""
    if not directory.is_dir():
        return []
    out: list[Harness] = []
    for p in sorted(directory.glob("*.toml")):
        text = p.read_text(encoding="utf-8")
        require_known_schema(text)
        if layer == "project":
            bad = operator_only_violations(tomllib.loads(text))
            if bad:
                raise PackError(
                    f"{p.name}: a cloned project may add detection, paths and facts, never a "
                    f"key that runs a process; operator-only {', '.join(bad)}"
                )
        out.append(harnessreg.parse(text, p.stem))
    return out


def load_descriptors(root: Path | str, user_home: Path | str | None = None) -> tuple[Harness, ...]:
    """Every descriptor the project sees, layers merged per id, later layers winning, in rank
    order. Refuses a project-layer descriptor that sets an operator-only key."""
    layers = [
        harnessreg.load(),  # packaged: shipped, already ranked
        tuple(_layer_descriptors(descriptor_dirs(root, user_home)[1][1], "user")),
        tuple(_layer_descriptors(descriptor_dirs(root, user_home)[2][1], "project")),
        tuple(_layer_descriptors(descriptor_dirs(root, user_home)[3][1], "local")),
    ]
    merged: dict[str, Harness] = {}
    for group in layers:
        for h in group:
            merged[h.id] = h
    ranks: dict[int, str] = {}
    for h in merged.values():
        if h.rank in ranks:
            raise harnessreg.DescriptorError(f"{h.id}: rank {h.rank} is also {ranks[h.rank]}'s")
        ranks[h.rank] = h.id
    return tuple(sorted(merged.values(), key=lambda h: h.rank))


def _is_url(source: str) -> bool:
    return isinstance(source, str) and "://" in source


def _fetch(url: str) -> bytes:
    return UrllibHttpClient().request("GET", url, timeout=30.0, max_bytes=MAX_PACK_BYTES).body


def load_pack(source: str | Path, *, digest: str | None = None) -> dict[str, Any]:
    """Load a company descriptor pack: data only, pinned by digest.

    A bare URL without a digest is refused (remote content is data, never instructions, and
    never silently changed). With a digest, the fetched or read content is checked against
    it and a mismatch keeps the content out. With a local path and no digest the file is
    parsed as-is. Returns the raw TOML tables."""
    if _is_url(str(source)):
        if not digest:
            raise PackError("a bare URL pack without a digest is refused; pin the digest first")
        data = _fetch(str(source))
    else:
        data = Path(source).read_bytes()
    digest = digest.lower() if digest else digest
    if digest:
        got = content_digest(data)
        if got != digest:
            raise PackError(f"pack digest mismatch: pinned {digest[:12]}…, content {got[:12]}…")
    return tomllib.loads(data if isinstance(data, str) else data.decode("utf-8"))
