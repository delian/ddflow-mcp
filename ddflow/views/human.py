"""Human renderings that BOTH surfaces need, keyed by `Outcome.kind`.

`core/outcome.py` has claimed since it was written that "the human surface is derived
from that same data by exactly one renderer in `views.human`". That was a description of
an intention: no such module existed, and neither did the `tests/test_views.py` it named.
This is the module, scoped honestly to what actually needs to be shared.

**Most human renderings do NOT belong here.** They live beside their command in
`surfaces/commands/<family>.py`, because only one surface renders them — the MCP
counterpart returns JSON. Moving all of them here would trade a 4,000-line `cli.py` for a
2,000-line `human.py` and call it layering.

What belongs here is the rendering whose TEXT is the wire body on both surfaces. `doctor`
is the case: its MCP tool has always returned the report as prose, because a list of
problems with advice attached is what an operator and an agent both want, so the renderer
cannot live in a surface without one surface reaching into the other. `board`, `replay`
and the markdown views are the same shape and already had somewhere to live —
`views/markdown.py` and `services/sessions.py` — which is why they are not duplicated
here.
"""

from __future__ import annotations

import textwrap
from typing import Any

#: `kind` -> renderer. Populated by `@renders`, and asserted non-empty and complete by
#: `tests/test_views.py` -- a registry nothing checks is a dict.
RENDERERS: dict[str, Any] = {}


def renders(kind: str):
    def wrap(fn):
        if kind in RENDERERS:
            raise RuntimeError(f"two renderers claim {kind!r}: {RENDERERS[kind]} and {fn}")
        RENDERERS[kind] = fn
        return fn

    return wrap


def render(out) -> str:
    """The text for an Outcome whose body IS text."""
    fn = RENDERERS.get(out.kind)
    if fn is None:
        raise KeyError(
            f"no human renderer for {out.kind!r}. Register one with "
            f"@renders({out.kind!r}) in views/human.py, or the result cannot be shown "
            f"to a person."
        )
    return fn(out)


@renders("doctor")
def doctor(out) -> str:
    """Everything wrong, everything worth knowing, and the counts behind both.

    Notes before problems on purpose. A note is context for reading the problems that
    follow — a stale index, an unreachable reviewer endpoint — and putting the problems
    first means the note explaining one of them arrives after it.
    """
    d = out.data
    lines = [
        f"events {d['events']} · items {d['items']} · agent {d['agent']} · repo {d['repo']}",
        f"index: {'stale (auto-rebuilds)' if d['index_stale'] else 'current'} · "
        f"fts5: {'yes' if d['fts'] else 'no (LIKE fallback)'}",
    ]
    lines += [f"  note: {n}" for n in d["notes"]]
    lines += [f"  PROBLEM: {p}" for p in d["problems"]]
    lines.append("")
    lines.append("Healthy." if not d["problems"] else f"{len(d['problems'])} problem(s).")
    return "\n".join(lines)


@renders("reviewers.list")
def reviewers_list(out) -> str:
    """Every reviewer, and the warning that decides whether `complete` will work.

    The unclassified note is not cosmetic: a reviewer with no known family cannot satisfy
    `[agent].reviewer_family_must_differ`, so completion refuses with a reason that reads
    as though it is about the review rather than about a missing `family = "..."` line.
    """
    d = out.data
    lines = [
        f"  {r['name']:<22} {r['family'] or '?':<12} gates={','.join(r['gates'])} "
        f"{'' if r['enabled'] else '(disabled) '}{r['base_url']} [{r['model']}]"
        for r in d["reviewers"]
    ]
    if d["unclassified"]:
        lines += [
            "",
            f"  {', '.join(d['unclassified'])} have no known family (shown as '?'), so they cannot",
            '  satisfy [agent].reviewer_family_must_differ. Set `family = "..."` on each',
            "  where it is declared (.ddflow/local/reviewers.toml, or .ddflow/config.toml),",
            "  or add the model name to [agent].families.",
        ]
    return "\n".join(lines)


@renders("reviewers.detect")
def reviewers_detect(out) -> str:
    d = out.data
    lines = [
        f"  {r['url']}  [{r['label']}]\n      model  {r['model']}\n      family {r['family']}"
        for r in d["found"]
    ]
    if d["written"]:
        lines.append(f"\nappended {d['count']} reviewer block(s) to {d['written']}")
    else:
        lines.append(
            "\nAdd to the git-ignored .ddflow/local/reviewers.toml (or re-run with "
            "--write; --shared commits them to .ddflow/config.toml for every clone):"
        )
        lines.append(d["blocks"])
    return "\n".join(lines)


@renders("config")
def config(out) -> str:
    """Every knob, its value, and WHERE that value came from.

    The source is the whole point: an operator debugging why a setting is not taking
    effect needs to know whether it came from the file, the environment or the default,
    and `[default]` next to the value they thought they set is the answer.
    """
    d = out.data
    lines: list[str] = []
    for row in d.get("rows", []):
        lines.append(f"{row['key']} = {row['value']!r}   [{row['source']}]")
        if d.get("explain") and row.get("doc"):
            lines += [f"    {ln}" for ln in textwrap.wrap(" ".join(row["doc"].split()), 76)]
    if d.get("path") and not d.get("rows"):
        # A WRITE: the answer is what changed and where.
        return f"{d.get('key', '')} = {d.get('literal', '')}".strip() or f"appended to {d['path']}"
    return "\n".join(lines)


@renders("setup")
def setup(out) -> str:
    """What adoption wrote, and the three steps that follow.

    The companion tail is the part worth having: a project that adopts ddflow and stops has
    a `standards` gate with nothing behind it and a `rules` gate reading no memory, and
    because an agent gate passes on an assertion that gap is invisible in exactly the way
    this design exists to prevent.
    """
    d = out.data
    agents = d.get("agents") or []
    tail = ""
    if d.get("refresh_docs"):
        return (
            "\n".join(f"  {x}" for x in d.get("actions", []))
            + "\n\nDriver docs and rules refreshed; MCP launches, hooks and command files "
            "were not touched."
        )
    if d.get("companions_ready"):
        tail += (
            f"\n\nInstalled here but not wired up: {', '.join(d['companions_ready'])}.\n"
            f"  ddflow companions add --agents {agents[0] if agents else 'claude'}"
        )
    if d.get("companions_absent"):
        tail += (
            f"\n\nNot installed: {', '.join(d['companions_absent'])} — "
            f"`ddflow companions` has the commands."
        )
    if out.exit != 0:
        # A refusal: never "adopted for" -- that line is what a reader stops at.
        return (
            "\n".join(f"  {x}" for x in d.get("actions", []))
            + f"\n\nddflow NOT fully adopted for: {', '.join(agents)} -- "
            "finish the SKIPPED step(s) above by hand, then re-run `ddflow adopt`." + tail
        )
    return (
        "\n".join(f"  {x}" for x in d.get("actions", []))
        + f"\n\nddflow adopted for: {', '.join(agents)}.\n"
        "  1. set your test command in .ddflow/gates.toml\n"
        "  2. ddflow phase add P1 --title '...'\n"
        "  3. tell your agent: implement phase P1" + tail
    )
