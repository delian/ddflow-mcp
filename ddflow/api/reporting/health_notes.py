"""The `doctor` findings that read the rules surface, the export targets, the untitled ids
and the near-duplicate sweep, split out of `health.py` so that module stays under the
area's module-line budget (B328649c6ed)."""

from __future__ import annotations

import re
from pathlib import Path

from ...config import Config
from ...core.events import is_older
from ...services.adopt import DUPLICATE, MISSING, NOT_BINDING, STALE, block_version, rules_status
from ...services.export import ops as X
from ...services.export.query import ExportError
from ..knowledge import pairs_from
from ..knowledge.pairs import _sweep_records


def _rules_findings(repo: Path, highest: str, problems: list[str], notes: list[str]) -> None:
    """The rules surface: MISSING, NOT_BINDING and DUPLICATE are PROBLEMs, a drifted or stripped
    block is a note (the agent has rules, they are just not current)."""
    for state in rules_status(repo):
        if not state.needs_attention:
            continue
        # A block a newer ddflow wrote is not rewritten by a refresh: its own line says so.
        line = (
            state.render()
            if state.newer
            else (
                f"{state.render()} — `ddflow adopt --refresh-docs` rewrites it "
                "(plain `ddflow adopt` also rewrites MCP launches)"
            )
        )
        # NOT_BINDING sits with MISSING: a rule the agent may never load is not a
        # milder version of a drifted one, it is the mechanism switched off.
        severe = state.state in (MISSING, NOT_BINDING, DUPLICATE)
        (problems if severe else notes).append(line)
        # A section that needs a refresh AND was written by a ddflow older than the one that
        # last worked on this log: say which, so the refresh is not mistaken for noise.
        wrote = block_version(repo / state.path) if state.state in (DUPLICATE, STALE) else None
        if wrote and is_older(wrote, highest):
            notes.append(
                f"{state.path}'s ddflow section is stamped {wrote}, older than {highest}, "
                f"the highest ddflow stamp in this log"
            )


def _only_ids(title: str) -> bool:
    """Empty, or nothing but id-shaped tokens and punctuation: `B30`, `B30.`, `+ B159`,
    `B62-B64`. A word without a digit is what makes a title say something."""
    return all(any(c.isdigit() for c in word) for word in re.findall(r"\w+", title))


#: How many untitled ids one note names before it counts the rest.
_UNTITLED_SHOWN = 8


def _export_target_notes(repo: Path, cfg: Config) -> list[str]:
    """A NOTE for each selected export target that is stale or hand-edited (B-export-enforce).

    Not a problem: regenerating is one command, and a doctor that fails on a document the log
    has simply moved past would block work. `[enforce].generated_views = "off"` silences it.
    """
    if cfg.enforce.generated_views == "off" or not cfg.export.documents:
        return []

    try:
        rows = X.listing(repo, cfg)
    except ExportError as exc:
        return [f"export: could not check the selected documents ({exc})"]
    return [
        f"export: {r['target']} ({r['doc']}) is {r['state']}"
        + (f": {r['detail']}" if r.get("detail") else "")
        + (
            f" (`ddflow export {r['doc']} --update`)"
            if r["state"] == "stale"
            else " (`ddflow export --check`; regenerating needs --force)"
        )
        for r in rows
        if r["selected"] and r["state"] in ("stale", "hand-edited")
    ]


def _untitled(st) -> list[str]:
    """ONE note naming the items whose title says nothing but their id.

    A migration that cannot find a title writes the id (bug B9e8ca361ea: 24 backlog
    items were titled `B30`, `B157`...), and every board, brief and gate prompt then
    shows a number. Nothing else here asks whether an item can be told apart from it.
    """
    ids = sorted(it.id for it in st.items.values() if not it.removed and _only_ids(it.title))
    if not ids:
        return []
    shown = ", ".join(ids[:_UNTITLED_SHOWN]) + (" ..." if len(ids) > _UNTITLED_SHOWN else "")
    return [
        f"{len(ids)} item(s) have no title of their own, only the id: {shown} — "
        f"`ddflow update <id> --title ...`"
    ]


#: The inline duplicate count is bounded: the all-pairs sweep grows with the log (~3 s at
#: 1559 records), so above this many records `doctor` points at `ddflow dupes` instead.
_DOCTOR_SWEEP_MAX = 1000
#: Stop the count once this many pairs are found; the note then says "at least".
_DOCTOR_PAIR_CAP = 200


def _dupe_note(st, cfg) -> list[str]:
    """How many near-duplicate pairs the log holds that nobody has settled (B-dupes-sweep).

    A NOTE, never a problem: below the ask threshold a score is a prompt to LOOK, not a
    verdict (R-dedupe-matchers), so failing `doctor` on one would be failing it on a
    question. Counted at the ASK threshold -- the actionable "likely duplicate" band,
    not the wide show floor -- and bounded by record count and pair cap so `doctor`
    stays fast; above the bound the note names `ddflow dupes` rather than paying the
    sweep. `off` skips it: a project that switched the check off does not want its cost.
    """
    if cfg.dedupe.on_match == "off":
        return []
    try:
        n_records = sum(1 for r in _sweep_records(st) if r["kind"] in cfg.dedupe.kinds)
        if n_records > _DOCTOR_SWEEP_MAX:
            return [
                f"{n_records} records: the inline near-duplicate count is skipped above "
                f"{_DOCTOR_SWEEP_MAX} — `ddflow dupes` lists the pairs"
            ]
        rows = pairs_from(
            st,
            cfg,
            floor=cfg.dedupe.ask_threshold,
            limit=_DOCTOR_PAIR_CAP,
            stop_after=_DOCTOR_PAIR_CAP,
        )
    except Exception as exc:  # an unreadable index must not take the report down
        return [f"near-duplicate sweep could not run ({type(exc).__name__}: {exc})"]
    if not rows:
        return []
    n = f"at least {_DOCTOR_PAIR_CAP}" if len(rows) >= _DOCTOR_PAIR_CAP else str(len(rows))
    return [
        f"{n} unsettled near-duplicate pair(s) at the ask threshold — `ddflow dupes` "
        f"lists them; `ddflow link A --duplicate-of B` settles one (or `--distinct`)"
    ]
