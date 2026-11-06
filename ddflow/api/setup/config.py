"""Configuration: read every knob with its source, or change one.

A config append is validated against the MERGED text, not against what is already on
disk. Validating the state you are replacing has checked nothing.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...config import ReviewConfig, is_source_tree
from ...core import outcome as O
from ...infra import tomlcfg as TC
from ...services import approval as AP
from ...services import configwrite as CW
from ...services.approvals import class_states as APCS
from ...services.approvals import consume as CN
from ...services.approvals.canon import Canon
from ...services.config_shadow import shadow_report
from ...views import default_toml as D
from ...views import human
from .._base import _load
from ..knowledge import session_note


@dataclass
class ConfigEdit:
    """One config change, or a read. `set`+`value` OR `append_toml`, never both.

    `local` sends the change to the git-ignored `.ddflow/local/config.toml`: this
    machine's endpoints, hosts, keys' variable names and sizing. Without it the change
    goes to the committed `.ddflow/config.toml`, which every clone receives.

    `defaults` reads the packaged `default.toml` instead of any project file, and `write`
    with it regenerates that file in ddflow's own source tree.
    """

    set: str = ""
    value: str = ""
    append_toml: str = ""
    filter: str = ""
    explain: bool = False
    local: bool = False
    defaults: bool = False
    write: bool = False


def _packaged_defaults(repo: Path, write: bool) -> O.Outcome:
    """`config defaults`: the packaged file, or with ``write`` the file regenerated.

    Only a source tree inside ``repo``: a worktree's `ddflow` on PATH is the primary's code,
    so its templates are another tree's, and an installed ddflow's are not a checkout at all,
    even in a `.venv` under the repository.
    """
    path = D.PACKAGED_DEFAULTS
    if not write:
        text = D.current()
        if not text:
            return O.failed("config", f"{path} is missing: this ddflow is damaged", text="")
        return O.ok("config", path=str(path), text=text)
    if not (is_source_tree(path) and path.resolve().is_relative_to(Path(repo).resolve())):
        return O.refused(
            "config",
            f"{path} is not this repository's source: run `python -m ddflow.views.default_toml` "
            "from the source tree it belongs to",
            path=str(path),
            text="",
        )
    changed = D.write()
    return O.ok(
        "config",
        path=str(path),
        changed=changed,
        text=f"{path}: {'rewritten' if changed else 'current'}",
    )


def _said(added: list[str]) -> str:
    if not added:
        return ""
    return "\nadded to .gitattributes (tracked -- commit it with the config):\n" + "\n".join(
        f"  {ln}" for ln in added
    )


def _extra_keys(cfg) -> set[str]:
    """The keys `[enforce].machine_local_keys` adds to the machine-local classification.

    Read through the attribute directly, not `getattr(..., None)`: the dead-knob ratchet
    (tests/test_ratchet_no_dead_knobs.py) is a NAME-based scan and cannot see a read that
    spells the knob as a `getattr` string literal, so it would report a knob that this very
    function consumes as dead (bug B21ec47765c). ``config.enforce`` is an `EnforceConfig`
    dataclass with `machine_local_keys` defaulting to ``[]``, so the attribute is always present.
    """
    return set(cfg.enforce.machine_local_keys)


def _default_layer(cfg, edit: ConfigEdit) -> str:
    """The layer ``edit`` writes to: the one it names, except that a machine-local key or
    a reviewer/companion-only TOML document without one defaults to the local layer
    (DX-machine-local-keys: how many workers THIS machine runs, and which reviewers and
    endpoints serve it, are not the next machine's answers). `[enforce].machine_local_keys`
    extends the shipped classification held in `services.configwrite`."""
    if edit.local:
        return "local"
    if edit.set:
        return CW.machine_local_layer(_extra_keys(cfg), edit.set) or "file"
    if not edit.append_toml:
        return "file"
    try:
        tables = set(tomllib.loads(edit.append_toml))
    except tomllib.TOMLDecodeError:
        tables = set()
    return "local" if CW.machine_local_tables(_extra_keys(cfg), tables) else "file"


def configure(
    repo: Path, edit: ConfigEdit | None = None, *, agent: str = "", approval: str = ""
) -> O.Outcome:
    """Read every knob with its source, or change one.

    An agent may drive a change through an operator's approval (verb config.set): with
    `--approval request` it files the request the operator answers anywhere, with
    `--approval ID` it applies the change and records the approval. A person configures
    directly and is unchanged. An append is validated against the MERGED text — see the
    module docstring for why validating what is already on disk checks nothing.
    """

    edit = edit or ConfigEdit()
    if edit.defaults:
        return _packaged_defaults(repo, edit.write)
    log, cfg, _st = _load(repo, agent)
    layer = _default_layer(cfg, edit)
    # Machine-local keys (parallelism, reviewers, companions) default to the local layer:
    # this machine's own answer must not reach the committed file silently (DX-machine-local-keys).
    defaulted = not edit.local and layer == "local"
    target = str(CW.config_file(repo, local=layer == "local"))

    stamp: dict[str, Any] = {}
    if edit.set and approval:
        if not AP.agent_marker(agent):
            return O.refused(
                "config",
                "--approval is an agent's route; a person configures it directly",
                key=edit.set,
                value=edit.value,
                rows=[],
                text="",
            )
        canon = Canon(
            verb="config.set",
            agent=log.agent_id,
            item=edit.set,
            target=edit.value,
            state=APCS.config_set_state(edit.set, edit.value, layer),
        )
        decision = CN.operator_gate(log, cfg, canon, approval_id=approval)
        if not decision.ok:
            return O.refused(
                "config",
                "refusing: " + decision.message,
                key=edit.set,
                value=edit.value,
                rows=[],
                text="",
            )
        stamp = decision.stamp

    if edit.set:
        # What a person typed: `5` and `true` are the literals they spell (`Spelled`).
        res = CW.apply_edit(
            repo, CW.SetPairs([(edit.set, CW.Spelled(edit.value))]), layer=layer, agent=agent
        )
        if res.error:
            # A key that is not plain (D-plain-keys) is refused, not failed: exit 3.
            done = O.refused if isinstance(res.error, CW.KeyRefused) else O.failed
            return done("config", res.error, key=edit.set, value=edit.value, rows=[], text="")
        literal = TC.literal(edit.value)
        shadow = shadow_report(repo, edit.set, layer)
        return O.ok(
            "config",
            key=edit.set,
            value=edit.value,
            literal=literal,
            path=target,
            local=layer == "local",
            rows=[],
            gitattributes_added=res.attributes_added,
            text=f"{edit.set} = {literal}  ({target})"
            + (
                f"\n  machine-local key {edit.set}: this machine's own answer, defaulted "
                f"to the local layer; [enforce].machine_local_keys extends which keys do"
                if defaulted
                else ""
            )
            + shadow["shadow_note"]
            + _said(res.attributes_added),
            **shadow,
            **({"approval": stamp} if stamp else {}),
        )

    if edit.append_toml:
        # Validated BEFORE writing: an agent composing TOML gets a parse error back as a
        # readable message instead of leaving the project with a config no later command
        # can load.
        res = CW.apply_edit(repo, CW.AppendText(edit.append_toml), layer=layer, agent=agent)
        if res.error:
            done = O.refused if isinstance(res.error, CW.KeyRefused) else O.failed
            return done("config", res.error, path="", rows=[], text="")
        return O.ok(
            "config",
            path=str(res.path),
            appended=True,
            local=layer == "local",
            rows=[],
            gitattributes_added=res.attributes_added,
            text=f"appended to {res.path}"
            + (
                "\n  machine-local document (reviewer/companion blocks): this machine's own "
                "reviewers and endpoints, defaulted to the local layer; [enforce]."
                "machine_local_keys extends which keys do"
                if defaulted
                else ""
            )
            + _said(res.attributes_added),
        )

    rows = [
        {"key": k, "value": v, "source": s, "doc": d}
        for k, v, s, d in cfg.explain()
        if not edit.filter or edit.filter in k
    ]
    out = O.ok("config", rows=rows, explain=edit.explain, filter=edit.filter, text="")
    out.data["text"] = human.render(out)
    return out


_BUDGET_KNOBS = ("review.max_rounds", "review.on_exceed", "review.delta_default")


def _budget_keys(edit: ConfigEdit) -> list[str]:
    """The `review.*` knobs this edit sets, parsed (not substring-matched): quoting,
    spacing and a commented-out header do not change what TOML says."""

    def norm(k: str) -> str:
        return ".".join(seg.strip().strip("\"'") for seg in k.split("."))

    # `configure` applies `set` and returns; an `append_toml` beside it is never written
    # (the probe is in tests/test_review_budget.py), so only the one that IS applied counts.
    if edit.set:
        return [k for k in _BUDGET_KNOBS if norm(edit.set) == k]
    try:
        table = tomllib.loads(edit.append_toml).get("review")
    except tomllib.TOMLDecodeError:
        return []
    if not isinstance(table, dict):
        return []
    return [f"review.{k}" for k in table if f"review.{k}" in _BUDGET_KNOBS]


def report_budget_change(
    repo: Path, edit: ConfigEdit, out: O.Outcome, *, agent: str = ""
) -> O.Outcome:
    """Tell the operator when an AGENT (the MCP surface) changed the review budget.

    The cap is the operator's: `ddflow_configure` accepts `review.max_rounds`,
    `review.on_exceed` and `review.delta_default` on either layer, so an operator without a shell can tune it, but
    an agent raising or disabling it to get more review rounds must not do so silently.
    The change is returned as a NOTE and recorded as a session note the operator reads
    in `session show` and the brief; a per-item exception is `ddflow review --force
    --reason`, which is recorded on the gate instead.
    """
    if out.exit != O.OK:
        return out
    touched = _budget_keys(edit)
    if not touched:
        return out

    default = ReviewConfig()
    local = bool(out.data.get("local", edit.local))
    layer = "machine-local (.ddflow/local)" if local else "shared (.ddflow/config.toml)"
    revert = "; ".join(
        f"`ddflow config --set {k} {str(getattr(default, k.split('.')[1])).lower()}"
        f"{' --local' if local else ''}`"
        for k in touched
    )
    note = (
        f"NOTE FOR THE OPERATOR: the review budget was changed over MCP "
        f"({', '.join(touched)}; {layer} layer). Shipped default: {default.max_rounds} full "
        f"rounds per gate, on_exceed = {default.on_exceed!r}, delta_default = "
        f"{str(default.delta_default).lower()}. If you did not ask for this, "
        f"revert it with {revert}. Recorded as a session note."
    )

    session_note(repo, "", note, agent=agent)
    out.data["text"] = (out.data.get("text") or "") + "\n" + note
    return out
