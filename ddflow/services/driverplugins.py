"""Driver plugins: third-party lane transports as entry points or external executables.

Operator decision D-DRV-Q1 (option C now): a third-party driver registers in one of two kinds.

* ``entry_point`` -- an in-process Python object, named ``package.module:attr``, that builds a
  ``Lane`` (the five-call contract in ``ddflow.adapters.lanes``); it is imported and checked
  against the ``Lane`` protocol before it is enabled.
* ``executable`` -- an external program speaking the versioned JSON-over-stdio protocol
  ``KNOWN_PROTOCOL_VERSIONS`` defines; it declares the version it speaks and the argv to run it.

Registration is operator-only (D-DRV-5, D-DRV-Q5): a plugin is declared in the operator's
own layers -- the user dir (``~/.config/ddflow/driver_plugins.toml``, or under
``$XDG_CONFIG_HOME``) and machine-local (``.ddflow/local/driver_plugins.toml``). The project
layer (``.ddflow/driver_plugins.toml``) is deliberately absent: a cloned project can describe
drivers, never register a program ddflow will run, so ``registration_files`` reads the operator
layers and not the project's.

Trust and sandbox: each plugin carries ``trust`` (``operator`` or ``third-party``). An
``operator`` plugin runs in-process (a ``Lane`` built by its entry point, like the in-tree
transports) with the operator's trust. A ``third-party`` plugin is external: it must be an
``executable`` (out-of-process) so its ``sandbox`` holds -- the exact env NAMES it may read,
the same rule as a ``LaunchSpec``: secrets travel by env NAME and their values stay out of
every repr, and an unlisted ambient name never reaches it. In-process code cannot be sandboxed
(in-process, ``os.environ`` is visible), which is why an in-process ``entry_point`` is
operator-only and a ``third-party`` plugin is refused unless it is an executable.

Nothing is enabled before conformance: the SDK checks the structure an entry point must honour
(it builds a ``Lane`` whose capabilities are all known), and the version an executable must
speak; a plugin that does not, or that declares a protocol version this ddflow does not speak,
is refused (``PluginError``), never run half-read. Full behavioural conformance is the offline
DRV-conformance suite's job; this module runs the registration-time checks. Major versions
define compatibility (the Terraform plugin protocol, research driver-architecture 4.5); a byte
this code does not know is "unavailable: upgrade ddflow".
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any

from ..adapters.env import DEFAULT as ENV
from ..adapters.lanes import CAPABILITIES, Lane

#: The JSON-over-stdio protocol majors this code speaks. A plugin that declares a version
#: outside this set is refused ("unavailable: upgrade ddflow"), never half-read.
KNOWN_PROTOCOL_VERSIONS = frozenset({1})

#: The Python entry-point group an in-process plugin may also register under (``attr`` builds a
#: ``Lane``). The registration files name the object directly; the group is for packaging.
ENTRY_POINT_GROUP = "ddflow.driver_plugins"

#: Trust labels. ``operator`` runs with the operator's trust; ``third-party`` is pinned to its
#: declared sandbox and is not enabled until it conforms.
TRUST_LABELS = ("operator", "third-party")

#: The keys a ``[[plugin]]`` table may carry. Any other key is refused so a typo never reads
#: as "not provided" (an ``operator`` typo'd ``sandox`` would silently drop the env allowlist).
_PLUGIN_KEYS = frozenset({"name", "kind", "trust", "sandbox", "protocol", "entry_point", "argv"})


class PluginError(ValueError):
    """A driver plugin is refused: unknown, a wrong layer, an unknown version, non-conforming."""


@dataclass(frozen=True)
class Plugin:
    """One registered driver plugin (registration is operator-only)."""

    name: str
    kind: str  #: "entry_point" | "executable"
    trust: str  #: "operator" | "third-party"
    sandbox: tuple[str, ...] = ()  #: env NAMES it may read; () = nothing inherited
    protocol: int = 1  #: executable: the JSON-over-stdio version it declares
    entry_point: str = ""  #: entry_point: "package.module:attr"
    argv: tuple[str, ...] = ()  #: executable: argv list, never a shell line


def user_dir() -> Path:
    """The user ddflow directory: ``$XDG_CONFIG_HOME/ddflow``, else ``~/.config/ddflow``."""
    base = ENV.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "ddflow"


def registration_files(
    root: Path | str, user_home: Path | str | None = None
) -> tuple[Path, ...]:
    """The operator-only layers a plugin may be declared in, weakest first: user, local.

    The project layer (``.ddflow/driver_plugins.toml``) is not here: D-DRV-Q5 keeps keys that
    run a process operator-only, so a cloned project can never register a program ddflow runs.
    """
    ud = Path(user_home) if user_home is not None else user_dir()
    root = Path(root)
    return (ud / "driver_plugins.toml", root / ".ddflow" / "local" / "driver_plugins.toml")


def _parse_plugin(source: str, table: dict[str, Any]) -> Plugin:
    """One ``[[plugin]]`` table into a ``Plugin``; a wrong key, shape, trust or version is a
    ``PluginError``. An unknown key is refused so a typo never silently reads as "not provided"."""
    unknown = sorted(set(table) - _PLUGIN_KEYS)
    if unknown:
        raise PluginError(f"{source}: unknown key(s) {', '.join(unknown)} on a [[plugin]] table")
    name = table.get("name")
    if type(name) is not str or not name:
        raise PluginError(f"{source}: a plugin entry needs a string name")
    kind = table.get("kind")
    if kind not in ("entry_point", "executable"):
        raise PluginError(f"{source}: {name}: kind must be 'entry_point' or 'executable'")
    trust = table.get("trust", "third-party")
    if trust not in TRUST_LABELS:
        raise PluginError(f"{source}: {name}: trust is {trust!r}, one of {TRUST_LABELS}")
    protocol = table.get("protocol", 1)
    if type(protocol) is not int:
        raise PluginError(f"{source}: {name}: protocol is the int the executable declares")
    if protocol not in KNOWN_PROTOCOL_VERSIONS:
        raise PluginError(
            f"{source}: {name}: protocol {protocol} is not one this ddflow speaks "
            f"({sorted(KNOWN_PROTOCOL_VERSIONS)}): unavailable: upgrade ddflow"
        )
    if kind == "entry_point" and trust == "third-party":
        raise PluginError(
            f"{source}: {name}: a third-party plugin is external: kind must be 'executable' so "
            "its sandbox holds, because in-process entry points are operator-only"
        )
    sandbox = table.get("sandbox", [])
    if not isinstance(sandbox, list) or not all(type(v) is str for v in sandbox):
        raise PluginError(f"{source}: {name}: sandbox is a list of env names")
    entry_point = table.get("entry_point", "")
    if type(entry_point) is not str:
        raise PluginError(f"{source}: {name}: entry_point is a 'package.module:attr' string")
    argv = table.get("argv", [])
    if not isinstance(argv, list) or not all(type(v) is str for v in argv):
        raise PluginError(f"{source}: {name}: argv is a list of strings, never a shell line")
    if kind == "executable" and not argv:
        raise PluginError(f"{source}: {name}: an executable plugin needs argv")
    if kind == "entry_point" and not entry_point:
        raise PluginError(f"{source}: {name}: an entry_point plugin names 'package.module:attr'")
    return Plugin(
        name=name,
        kind=kind,
        trust=str(trust),
        sandbox=tuple(sandbox),
        protocol=int(protocol),
        entry_point=str(entry_point),
        argv=tuple(argv),
    )


def discover_plugins(
    root: Path | str, user_home: Path | str | None = None
) -> dict[str, Plugin]:
    """Every plugin the operator has registered, keyed by name, later layers winning.

    A declaration an operator layer makes with an unknown key, kind, trust label or protocol
    version is refused (a typo must not silently read as "not provided"), and an unknown
    protocol version is "unavailable: upgrade ddflow", never half-read.
    """
    out: dict[str, Plugin] = {}
    for path in registration_files(root, user_home):
        if not path.is_file():
            continue
        try:
            raw = tomllib.loads(path.read_text("utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise PluginError(f"{path.name}: not valid TOML") from exc
        entries = raw.get("plugin")
        if entries is None:
            continue
        if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
            raise PluginError(f"{path.name}: name plugins as [[plugin]] tables")
        for table in entries:
            plugin = _parse_plugin(path.name, table)
            out[plugin.name] = plugin
    return out


def _import_entry_point(spec: str) -> Any:
    """Resolve ``"package.module:attr"`` to the object it names (attr may be dotted)."""
    module, sep, attr = spec.partition(":")
    if not sep or not module or not attr:
        raise PluginError(f"an entry point is 'package.module:attr'; got {spec!r}")
    obj: Any = import_module(module)
    for part in attr.split("."):
        try:
            obj = getattr(obj, part)
        except AttributeError as exc:
            raise PluginError(f"entry point {spec!r} has no attribute {part!r}") from exc
    return obj


def _build_lane(spec: str) -> Lane:
    """The ``Lane`` a conforming entry point names: the object itself, or what calling it gives."""
    obj = _import_entry_point(spec)
    lane = obj() if callable(obj) else obj
    if isinstance(lane, Lane):
        return lane
    raise PluginError(f"{spec!r} does not build a Lane (start/events/steer/stop/status)")


def load(
    name: str,
    root: Path | str | None = None,
    user_home: Path | str | None = None,
) -> Lane:
    """Enable ``name``: discover it, run conformance, then build its in-process ``Lane``.

    Raises ``PluginError`` when ``name`` is not registered, when it is an external executable
    (run through the JSON-stdio transport, not in-process), or when it does not build a Lane
    with only known capabilities. ``root`` defaults to the current directory.
    """
    root = Path(root) if root is not None else Path.cwd()
    plugins = discover_plugins(root, user_home)
    plugin = plugins.get(name)
    if plugin is None:
        known = ", ".join(sorted(plugins)) or "(none)"
        raise PluginError(f"no driver plugin {name!r}; registered: {known}")
    if plugin.kind == "executable":
        names = ", ".join(plugin.sandbox) or "none"
        raise PluginError(
            f"{name}: an external executable is registered (protocol {plugin.protocol}, sandbox "
            f"env names: {names}); it runs through the JSON-stdio Lane transport, which passes "
            "only those names — an in-process Lane is refused for it"
        )
    lane = _build_lane(plugin.entry_point)
    caps = getattr(lane, "capabilities", frozenset())
    if not caps <= CAPABILITIES:
        raise PluginError(
            f"{name}: capabilities {sorted(caps)} are not all in {sorted(CAPABILITIES)}"
        )
    return lane
