"""A route (harness x model x endpoint) as data, rendered into a lane-PRIVATE provider block.

The endpoint is a catalog route (`modelreg`: shipped rows plus the git-ignored
`.ddflow/local/models.toml`): ``endpoint`` fact (kind, base_url, api_key_env NAME, served
model) and the facts ``window``, ``max_output``, ``tool_call``, ``streaming``, ``concurrency``.
No second registry. The block goes under the lane's private HOME (``<home>/.config/kilo`` or
``.../opencode``), never into the project: kilo refuses ``{env:NAME}`` in project config, and a
project file would leak an address. Keys travel by env NAME only; no value is read here.
A tool-call capability that is not believable refuses the launch (research
R-swarm-heterogeneous-lanes: unproven tool calls mean unqualified, not piloted).
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from ...infra import paths
from .. import facts, modelreg
from ..lane_driver import Result, unavailable

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DATA = "harness-provider.toml"
_FACT_KEYS = ("window", "max_output", "tool_call", "streaming", "concurrency")
#: A provider id inside the lane's config: fixed prefix, so it never shadows a built-in one.
PROVIDER_PREFIX = "ddflow-"


@dataclass(frozen=True)
class Endpoint:
    route: str
    base_url: str
    model: str  #: the id the server serves
    api_key_env: str = ""  #: env NAME; "" = a keyless local server
    window: int | None = None
    max_output: int | None = None
    tool_call: bool = False  #: believable (verified/documented) and true
    streaming: bool | None = None
    concurrency: int | None = None  #: parallel requests the endpoint takes (scheduler cap)
    free: bool = False  #: class local: price zero

    @property
    def provider(self) -> str:
        return PROVIDER_PREFIX + self.route

    def __repr__(self) -> str:  # the address is local-layer data: keep it out of logs
        return f"Endpoint(route={self.route!r}, model={self.model!r}, key_env={self.api_key_env!r})"


def layout(harness: str) -> dict[str, Any] | None:
    """`{dir, file, schema, template, <facts>}` of a harness that takes a private provider block."""
    doc = tomllib.loads((paths.package_dir() / "models" / "routes" / _DATA).read_text("utf-8"))
    return doc.get("harness", {}).get(harness)


def data_problems() -> list[str]:
    """Defects of the shipped harness-provider facts (shared fact checker)."""
    doc = tomllib.loads((paths.package_dir() / "models" / "routes" / _DATA).read_text("utf-8"))
    out = []
    for hid, row in doc.get("harness", {}).items():
        for key, node in row.items():
            if facts.is_fact(node):
                out += facts.problems(f"harness.{hid}.{key}", node, doc.get("sources", {}))
    return out


def _val(cat: modelreg.Catalog, model: str, key: str, route: str) -> Any:
    return modelreg.read(cat, model, key, route=route).value


def _route_basics(
    row: dict[str, Any] | None, route: str, model: str
) -> tuple[str, str, str] | Result:
    """(base_url, key env name, served model) of an endpoint row, or what is wrong with it."""
    node = (row or {}).get("endpoint")
    v = node.get("v") if isinstance(node, dict) else None
    if not isinstance(v, dict) or v.get("kind") != "openai":
        return unavailable(f"route {route!r} is not an OpenAI-compatible endpoint")
    url, key = str(v.get("base_url") or ""), str(v.get("api_key_env") or "")
    served = model or str(v.get("model") or "")
    if not re.match(r"^https?://[^/@\s]+(/|$)", url):
        return unavailable(f"route {route!r}: base_url missing or carries credentials")
    if not served:
        return unavailable(f"route {route!r}: no served model id (set endpoint.model)")
    if key and not _ENV_NAME.match(key):
        return unavailable(f"route {route!r}: api_key_env must be a variable NAME, not a value")
    return url, key, served


def _int(x: Any) -> int | None:
    return x if isinstance(x, int) and not isinstance(x, bool) else None


def resolve(
    cat: modelreg.Catalog, route: str, model: str = "", *, require_tools: bool = True
) -> Endpoint | Result:
    """The route as an `Endpoint`, or an `unavailable` Result saying what is missing."""
    if route not in cat.route:
        return unavailable(f"no route {route!r} in the catalog")
    basics = _route_basics(cat.route[route], route, model)
    if isinstance(basics, Result):
        return basics
    url, key, served = basics
    f = {k: _val(cat, served, k, route) for k in _FACT_KEYS}
    if require_tools and f["tool_call"] is not True:
        return unavailable(
            f"route {route!r}/{served}: tool calls are not verified; run the live probe "
            "(needs the operator's consent) and record tool_call as verified"
        )
    return Endpoint(
        route,
        url,
        served,
        key,
        _int(f["window"]),
        _int(f["max_output"]),
        f["tool_call"] is True,
        f["streaming"] if isinstance(f["streaming"], bool) else None,
        _int(f["concurrency"]),
        _val(cat, served, "price_in", route) == 0,
    )


def render(ep: Endpoint, harness: str) -> str | Result:
    """The provider block as JSON text for ``harness`` (opencode or kilo)."""
    lay = layout(harness)
    if lay is None:
        return unavailable(f"harness {harness!r} has no private provider layout")

    env = Environment(
        loader=FileSystemLoader(str(paths.package_dir() / "templates")),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    text = env.get_template(lay["template"]).render(
        schema=lay["schema"],
        provider=ep.provider,
        name=f"ddflow lane {ep.route}",
        base_url=ep.base_url,
        api_key_env=ep.api_key_env,
        model=ep.model,
        tool_call=ep.tool_call,
        window=ep.window,
        max_output=ep.max_output,
    )
    json.loads(text)  # a template defect is an error here, not in the harness
    return text


def write_private(ep: Endpoint, harness: str, home: Path) -> Path | Result:
    """Write the block to ``<home>/<dir>/<file>`` (0600) and return the path. ``home`` is the
    lane's private home (``LaunchSpec.home``); it must not be inside a git work tree root's
    tracked project config, which is the caller's directory, not this one."""
    lay, text = layout(harness), render(ep, harness)
    if isinstance(text, Result) or lay is None:
        return text if isinstance(text, Result) else unavailable(f"no layout for {harness!r}")
    target = home / lay["dir"] / lay["file"]
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        os.fchmod(fh.fileno(), 0o600)  # a pre-existing file keeps its old mode otherwise
        fh.write(text)
    return target


def launch_env(ep: Endpoint) -> dict[str, str]:
    """`LaunchSpec.env` for the key: child variable -> SOURCE NAME (the value is read at launch)."""
    return {ep.api_key_env: ep.api_key_env} if ep.api_key_env else {}


def model_arg(ep: Endpoint) -> str:
    """The ``provider/model`` string for ``--model``."""
    return f"{ep.provider}/{ep.model}"


def probe_plan(ep: Endpoint, harness: str) -> Result:
    """The live probe, as the one thing not done offline. Always `unavailable` here: it calls the
    endpoint and uses a key, which needs the operator's consent."""
    return unavailable(
        f"live tool-call probe for {ep.route!r}/{ep.model} with {harness}: ask the operator; "
        "run `<harness> run --model "
        f"{model_arg(ep)} 'call the tool'` in a scratch dir, then record tool_call verified"
    )


@dataclass(frozen=True)
class Prepared:
    """What a launcher needs of an endpoint: pass ``home`` as `LaunchSpec.home`, ``env`` as
    `LaunchSpec.env` and ``model`` to the harness's model flag."""

    endpoint: Endpoint
    home: Path
    config: Path
    env: dict[str, str]
    model: str
    probe: Result  #: the live tool-call probe: `unavailable` until the operator runs it


def prepare(
    cat: modelreg.Catalog,
    route: str,
    harness: str,
    home: Path,
    model: str = "",
    *,
    require_tools: bool = True,
) -> Prepared | Result:
    """Resolve the route and write its private provider block under ``home`` in one call."""
    bad = data_problems()
    if bad:
        return unavailable(f"harness provider data is malformed: {bad[0]}")
    ep = resolve(cat, route, model, require_tools=require_tools)
    if isinstance(ep, Result):
        return ep
    cfg = write_private(ep, harness, home)
    if isinstance(cfg, Result):
        return cfg
    return Prepared(ep, home, cfg, launch_env(ep), model_arg(ep), probe_plan(ep, harness))
