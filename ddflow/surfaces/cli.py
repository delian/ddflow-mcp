"""The ddflow command line — the portable surface every agent can drive.

Design commitments, each of which exists so that ANY agent (or a human, or CI) can use
this without special support:

* **Everything is available as a subprocess call.** MCP is an optional convenience
  layer over the same functions, never a requirement. An agent with nothing but a shell
  can run the entire workflow.
* **``--json`` on every read command.** Agents parse; humans read. Offering both from
  one code path stops the two drifting.
* **A uniform exit vocabulary:** ``0`` healthy · ``1`` real failure · ``2`` could not
  run / nothing to do · ``3`` coordination refused. ``2`` is never collapsed into ``0``.
  "Nothing is ready" and "everything is fine" are different facts and an agent that
  cannot tell them apart will invent work.
* **Refusals name the alternative.** A command that says no says what to do instead;
  that is the difference between a blocked agent and a re-ordered one.
"""

from __future__ import annotations

import argparse
import os
import sys

from ..api import items as A_ITEMS
from ..core import clock
from ..core.model import fold
from ..core.outcome import INTERRUPTED, exit_for
from ..services import gates as G
from .commands.config import (  # noqa: F401  -- moved out of this module
    _config_set,
    _workflow_problems,
    _write_config,
)
from .context import (
    FAIL,
    NOTHING,
    OK,
    REFUSED,
    Ctx,
    _csv,
    _require_item,
)
from .parsers import REGISTER_ORDER
from .parsers._common import GLOBS_HELP, _Globs, _positive_int  # noqa: F401
from .registry import Notices, SuggestingParser, used_aliases
from .render import emit_json
from .tools import TOOLS
from .vocabulary import provide


def cmd_item_update(a, c: Ctx) -> int:
    # Through the api, like `ddflow_update` -- this wrote the event itself, so a field
    # the api validates (a release line that does not exist) went unchecked here.
    out = A_ITEMS.update(
        c.repo,
        a.id,
        A_ITEMS.ItemEdit(
            title=a.title,
            body=a.body,
            needs=None if a.needs is None else _csv(a.needs),
            # The raw values: the api reads them once. Parsed here as well, a JSON
            # array's element holding a comma was split by the second read (roborev).
            globs=a.globs,
            tags=None if a.tags is None else _csv(a.tags),
            priority=a.priority,
            line=a.line,
            resources=None if a.resources is None else _csv(a.resources),
            # Relative to where the caller stands, like any path typed in a shell.
            # An empty value stays empty, for the api to refuse: abspath("") is the cwd.
            worktree=a.worktree and os.path.abspath(a.worktree),
        ),
        agent=c.requested_agent,
    )
    if out.exit != OK:
        print(out.reason, file=sys.stderr)
        return out.exit
    msg = f"{a.id} updated: {', '.join(out.data['changed'])}"
    if out.data.get("branch"):
        msg += f"\n  now bound to {out.data['worktree']} on {out.data['branch']}"
    if "globs" in out.data["fields"]:
        # `--globs` REPLACES the list. Say what that took away: an agent widening its
        # claim with only the new paths otherwise drops the old ones from every
        # conflict check without a word (Bd8038b08a1).
        msg += f"\n  globs now: {', '.join(out.data['fields']['globs']) or '(none)'}"
        if out.data["globs_dropped"]:
            msg += (
                f"\n  dropped:   {', '.join(out.data['globs_dropped'])}  (--globs replaces "
                f"the list; pass every glob, old and new, to keep them)"
            )
    c.out(msg, out.body(("id", "changed", "globs_dropped")))
    return OK


def cmd_approve(a, c: Ctx) -> int:
    """A person clears, or refuses, a human-approval gate.

    CLI ONLY, on purpose, and `surfaces/exemptions.py` records the exemption with its
    reason (`tests/test_mcp_parity.py` enforces it). A human checkpoint an agent can satisfy through the MCP surface is not a
    human checkpoint — it is a second `gate record` with a longer name.
    """
    # `_require_item` FIRST, like every sibling. Without it `approve` was the one
    # gate-writing path with no existence check: `_h_gate` folds through `_item`, which
    # CREATES an item for an unknown subject, so a typo'd id printed "approved", exited
    # 0, and materialised a phantom task carrying a human approval -- while the item the
    # operator meant to approve stayed unapproved. That is the exact opposite of what
    # this command is for.
    if _require_item(c, a.id) is None:
        return FAIL
    try:
        line = G.approve(
            c.log,
            c.cfg,
            a.id,
            a.gate,
            gates=c.gates,
            note=a.note or "",
            reject=bool(a.reject),
            reason=a.reason or "",
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return FAIL
    c.out(line, {"id": a.id, "gate": a.gate, "approved": not a.reject, "line": line})
    return OK


def cmd_progress(a, c: Ctx) -> int:
    """What work has actually been done, aggregated from the log."""
    from ..api import progress as _progress
    from ..core import progress as PR

    out = _progress(c.repo, a.id or "")
    if out.exit == FAIL:
        print(out.reason, file=sys.stderr)
        return FAIL
    if c.json:
        # The ROW ARRAY, unchanged. `ddflow progress --json` has always emitted a list
        # and callers index it; the Outcome carries more, and the `payload` entry on the
        # MCP tool keeps that surface identical too.
        emit_json(out.data["rows"])
        return OK

    rows_d = out.data["rows"]
    if not rows_d:
        print("No work recorded yet.")
        return NOTHING
    # The TABLE renders from the wire rows -- no second fold. `summary()` carries every
    # column: counts for attempts/commits, seconds held, the holder list.
    print(f"{'item':<14} {'state':<10} {'att':>3} {'held':>9} {'gates':>5} {'commits':>7}  holders")
    for d in rows_d:
        secs = d["held_seconds"]
        held = clock.fmt_age(secs, places=1) if secs else "-"
        print(
            f"{d['item']:<14} {d['state']:<10} {d['attempts']:>3} {held:>9} "
            f"{d['gate_runs']:>5} {d['commits']:>7}  "
            f"{', '.join(sorted(set(d['holders']))) or '-'}"
        )
    if a.id:
        # The ONLY path that folds again, and only for one item. The per-attempt detail
        # (who held it, how it ended, gates passed and failed) is richer than the wire
        # summary, which flattens `attempts` to a count -- so this needs the objects and
        # the table above does not. Scoped to `--id` rather than paid on every call.
        events = c.log.read_all()
        rows = [r for r in PR.work(events, fold(events, strict=False)).values() if r.item == a.id]
        r = rows[0]
        print(f"\n{r.item} — {r.title}")
        for i, att in enumerate(r.attempts, 1):
            print(
                f"  attempt {i}: {att.holder} · {clock.fmt_age(att.seconds, places=1)} · "
                f"ended {att.ended_by or 'still open'} · "
                f"{att.gates_passed} passed / {att.gates_failed} failed"
            )
        for gate, outcomes in sorted(r.gate_outcomes.items()):
            print(f"  gate {gate:<14} {' -> '.join(outcomes)}")
    total = sum(d["held_seconds"] for d in rows_d)
    print(
        f"\n{len(rows_d)} item(s) · {total / 3600:.1f} agent-hours recorded · "
        f"{sum(d['commits'] for d in rows_d)} commit(s)"
    )
    return OK


def cmd_loops(a, c: Ctx) -> int:
    """Report circular references and runtime loops. Exit 2 when there are none.

    Renders the SAME `Outcome` the MCP surface returns, rather than computing a second
    view of the same answer. That is the B37 shape: one description of a result, two
    presentations derived from it — not two presentations kept in step by hand.
    """
    from ..api import loops as _loops
    from ..core import progress as PR

    out = _loops(c.repo)
    if c.json:
        emit_json(out.data["findings"])
        return out.exit
    if not out.data["findings"]:
        print(
            f"No loops detected ({out.data['events']} events, "
            f"{out.data['items']} items).\nChecked: " + ", ".join(out.data["checked"]) + "."
        )
        return out.exit
    # Reconstructed from the dicts the Outcome already carries. Calling `PR.detect`
    # again here would re-read and re-FOLD the whole log for a second copy of an answer
    # already in hand -- turning one O(events) pass into two, on the surface whose
    # entire job is to render what the layer below computed.
    for f in (PR.LoopFinding(**d) for d in out.data["findings"]):
        print(f"\n{f.render()}")
    print(
        f"\n{out.reason}. Thresholds are [loops] knobs; `ddflow config --explain --filter loops`."
    )
    return out.exit


#: How each event kind reads in a timeline. Absent kinds fall back to the kind name,
#: which is honest — a new event type shows up as itself rather than being silently
#: dropped from the history, which is the failure `replay` had with decisions.
def cmd_mcp(a, c: Ctx) -> int:
    from ..surfaces.mcp import serve

    # The caller's own start (--repo, DDFLOW_REPO or cwd), as ddflow-mcp passes it: without it every
    # called_from-aware tool treats the caller as standing in the primary (Bc1fe69741f).
    serve(c.repo, called_from=c._start)
    return OK


class _PrintVersion(argparse.Action):
    """`ddflow --version`. Reads `SERVER_INFO`, what the server reports at handshake; it is
    built from `ddflow.__version__`, the one declared version, so the two cannot differ."""

    def __call__(self, parser, namespace, values, option_string=None):
        from .mcp import SERVER_INFO

        print(f"ddflow {SERVER_INFO['version']}")
        parser.exit()


def _accept_global_options_anywhere(root: argparse.ArgumentParser) -> None:
    """`--repo`, `--agent` and `--json` after the subcommand as well as before it.

    They lived on the root parser only, so `ddflow brief --agent X` failed "unrecognized
    arguments" while `ddflow --agent X brief` worked -- and agents append flags, told by
    every driver to "pass --agent on every call" without being told where (B9811d8617c).
    Copied onto every subparser, nested ones included, with `default=SUPPRESS`: given
    after the subcommand it sets the same attribute (and wins over one given before),
    and absent there it leaves the root's value alone instead of resetting it.
    """
    # Derived from the root, so a global option added there later is accepted after the
    # subcommand too, instead of drifting from a second hand-kept list.
    mirrored = []
    for action in root._actions:
        if not action.option_strings or isinstance(
            action, (argparse._HelpAction, argparse._SubParsersAction, _PrintVersion)
        ):
            continue
        kwargs: dict = {"dest": action.dest, "default": argparse.SUPPRESS}
        if isinstance(action, argparse._StoreTrueAction):
            kwargs["action"] = "store_true"
        elif type(action) is not argparse._StoreAction:
            raise TypeError(f"global option {action.option_strings} cannot be mirrored")
        mirrored.append((action.option_strings, kwargs))
    seen: set[int] = set()

    def walk(parser: argparse.ArgumentParser) -> None:
        for action in parser._actions:
            if not isinstance(action, argparse._SubParsersAction):
                continue
            for sub in action.choices.values():
                if id(sub) in seen:  # an alias is the same parser under another name
                    continue
                seen.add(id(sub))
                for flags, kwargs in mirrored:
                    if not set(flags) & set(sub._option_string_actions):
                        sub.add_argument(*flags, help=argparse.SUPPRESS, **kwargs)
                walk(sub)

    walk(root)


def build_parser() -> argparse.ArgumentParser:
    """The root parser and its global options; each command group's subcommands come from
    its own module in `surfaces/parsers/`, registered in `ddflow --help` order. The global
    options are then copied onto every subparser (`_accept_global_options_anywhere`)."""
    p = SuggestingParser(
        prog="ddflow",
        description="A portable work-queue kernel for AI coding agents. "
        "The event log is the source of truth; everything else is derived.",
    )
    p.add_argument(
        "--repo", help="repository root (default: cwd, resolved to the primary checkout)"
    )
    p.add_argument("--agent", help="agent identity (default: host-pid). Shards the log.")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument(
        "--allow-older-version",
        action="store_true",
        dest="allow_older",
        help="let THIS session write although this ddflow is older than the one that last "
        "worked on the log (the skew guard); needs --reason, is recorded, and only the "
        "operator's insistence justifies it",
    )
    p.add_argument(
        "--reason",
        dest="skew_reason",
        default="",
        help="with --allow-older-version: why (a command's own --reason is used if it has one)",
    )
    # There was no such flag at all, and CI's build step -- `python -m ddflow --version`
    # against the built wheel -- failed with "the following arguments are required: cmd".
    # An Action rather than argparse's `version=` string: it runs, and exits, before the
    # required subcommand is checked, and it imports the version only when asked -- the
    # parser is built on every invocation and must not drag the MCP module in.
    p.add_argument("--version", action=_PrintVersion, nargs=0, help="print the version and exit")
    s = p.add_subparsers(dest="cmd", required=True)

    for register in REGISTER_ORDER:
        register(s)
    _accept_global_options_anywhere(p)
    return p


#: The aliases this process has already said are deprecated (one CLI process is one session).
_NOTICES = Notices()


# What the stale-reference scan (`doctor`) checks names against: this parser and the tool table.
provide(parser=build_parser, tools=TOOLS)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # What was typed, for the commands a refusal tells the caller to run instead.
    args._argv = list(sys.argv[1:] if argv is None else argv)
    # An old command, group or flag name still works (D-compat): said once, on stderr, so
    # `--json` output stays parseable.
    for alias in _NOTICES.fresh(used_aliases(parser, args, args._argv)):
        print(f"ddflow: {alias.notice()}", file=sys.stderr)
    try:
        ctx = Ctx(args)
        if getattr(args, "allow_older", False):
            reason = getattr(args, "skew_reason", "") or str(getattr(args, "reason", "") or "")
            if ctx.log.override_skew(reason) is None:
                print(
                    "ddflow: nothing to override (no skew); --allow-older-version ignored",
                    file=sys.stderr,
                )
        return int(args.fn(args, ctx))
    except BaseException as exc:
        # One table with MCP (`exit_for`, B5f3a650c40): a refusal says only its message
        # (the remedy), an error names its kind, and anything else is a bug -- re-raised.
        code = exit_for(exc)
        if code is None:
            raise
        if code != INTERRUPTED:
            print(str(exc) if code == REFUSED else f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return code


if __name__ == "__main__":
    raise SystemExit(main())
