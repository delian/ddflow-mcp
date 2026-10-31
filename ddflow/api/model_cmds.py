"""`ddflow model show NAME` (DRV-cli): the catalog row with provenance, and what an unknown
model gets. Read-only. Routes are never shown (they carry endpoint addresses and key names,
`modelreg._ROUTE_META`); what is shown goes through the network redaction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core import outcome as O
from ..core.redact import network_leaves, redact_network
from ..services import modelreg as MR

LOCAL = Path(".ddflow") / "local"
_SKIP = ("endpoint",)


def _source(cat: MR.Catalog, sid: Any) -> str:
    row = cat.sources.get(sid) if isinstance(sid, str) else None
    where = (row or {}).get("url") or (row or {}).get("path") or ""
    return f"{sid} ({where})" if where else str(sid or "no source")


def _facts(cat: MR.Catalog, table: str, rid: str, row: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for key, node in sorted(MR._fact_nodes(row)):
        if key in _SKIP:
            continue
        out.append(
            {
                "table": table,
                "row": rid,
                "key": key,
                "v": node.get("v"),
                "status": node.get("s"),
                "source": _source(cat, node.get("src")),
                "date": node.get("d", ""),
                "local": f"{table}.{rid}" in cat.local_ids,
            }
        )
    return out


def model_show(repo: Path, name: str) -> O.Outcome:
    if not (name or "").strip():
        return O.failed("model_show", "model show needs a model name")
    cat = MR.load(local_dir=Path(repo) / LOCAL)
    res = MR.resolve(cat, name)
    rows = []
    if res.row:
        rows += _facts(cat, "model", res.row, cat.model[res.row])
    if res.family != MR.UNKNOWN:
        rows += _facts(cat, "family", res.family, cat.family.get(res.family, {}))
    lines = [f"{name}: family {res.family}, vendor {res.vendor or '-'}, matched by {res.how}"]
    if res.how == "unknown":
        lines.append(
            "  unknown model: resolves to family 'unknown', never to a neighbour by name; "
            "every fact reads 'not verified' until a row is added to .ddflow/local/models.toml"
        )
    elif not res.row:
        lines.append("  no model row: only the family's facts apply; a missing key is not verified")
    for r in rows:
        mark = " [local]" if r["local"] else ""
        lines.append(
            f"  {r['table']}.{r['row']}.{r['key']} = {r['v']!r} {r['status']} "
            f"src={r['source']} d={r['date']}{mark}"
        )
    rows = network_leaves(rows)  # the wire body is as clean as the message
    msg = redact_network("\n".join(lines))
    return O.ok(
        "model_show",
        model=name,
        family=res.family,
        how=res.how,
        facts=rows,
        message=msg,
    )
