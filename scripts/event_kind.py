#!/usr/bin/env python3
"""List or apply the hand edits a new event kind needs (DX-event-kind-scaffold).

    scripts/event_kind.py check [KIND ...]     what each kind still lacks (default: every kind)
    scripts/event_kind.py add KIND --why TEXT [options]

A kind declares everything about itself in its OWN `ddflow/templates/events/<owner>.toml`
``[[kind]]`` table -- the field schema, the `text`/`lookup` redaction rows and the
`checked`/`exempt` classification -- so adding one needs exactly three edits, each found
otherwise by a failing test:

  1. handler     `core/model.py` HANDLERS -- code only you can write; `check` reports it
  2. declaration `ddflow/templates/events/<owner>.toml`, the [[kind]] with the fields below
  3. fragment    `ddflow/templates/upgrade/unreleased/event_kind_added.<kind>.toml`

The registry, the `tests/fixtures/event_kinds.json` snapshot, `core/redact/log.py` and the
coherence tables of `tests/test_coherence_coverage.py` are DERIVED from those declarations,
so none of those shared files is edited.

`add` writes 2 and 3 and leaves 1 to you; it never edits an entry that exists. Options
(repeatable): --field NAME=TYPE[,TYPE] a field of the payload; --text NAME a free-text field;
--lookup NAME=WHY a field compared or opened; --checked API.FN the API add function that
duplicate-checks the kind, or --exempt REASON why the kind files no checkable text (over 20
characters); --owner NAME the subsystem file the kind is declared in (default: the kind's
first word).

Run from the repository root with the project's environment (`uv run`).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ddflow.core import eventkinds as EK  # noqa: E402
from ddflow.core.model import HANDLERS  # noqa: E402

EVENTS = ROOT / "ddflow" / "templates" / "events"
FRAGMENTS = ROOT / "ddflow" / "templates" / "upgrade" / "unreleased"
MIN_REASON = 20
MIN_DOC = 10
TYPES = set(EK.TYPES)


def _declared() -> dict[str, EK.EventKind]:
    return {k.name: k for k in EK.load_dir(EVENTS)}


def _released(kind: str) -> bool:
    """Whether the shipped manifest already announces `kind`."""
    from ddflow.services import upgrade_manifest as UM

    try:
        return kind in UM.replay(UM.load())[1]
    except Exception:
        return False


def lacks(kind: str, by_name: dict[str, EK.EventKind]) -> list[str]:
    """What `kind` still lacks of its declaration and its handler, one line each."""
    out: list[str] = []
    if kind not in HANDLERS:
        out.append("handler: not in core/model.py HANDLERS (write the fold, then register it)")
    if kind not in by_name:
        out.append("registry: declared in no ddflow/templates/events/<owner>.toml")
    if not (FRAGMENTS / f"event_kind_added.{kind}.toml").exists() and not _released(kind):
        out.append(f"fragment: no unreleased/event_kind_added.{kind}.toml")
    return out


def _q(s: str) -> str:
    return json.dumps(s)


def _fields_arg(specs: list[str]) -> dict[str, list[str]]:
    """The `--field NAME=TYPE[,TYPE]` rows, sorted types, or refuse a bad one."""
    out: dict[str, list[str]] = {}
    for spec in specs:
        name, _, types = spec.partition("=")
        ts = sorted({t.strip() for t in types.split(",") if t.strip()})
        if not name or not ts or not set(ts) <= TYPES:
            raise SystemExit(f"--field {spec!r}: want NAME=TYPE[,TYPE], types {sorted(TYPES)}")
        out[name] = ts
    return out


def _lookup_arg(specs: list[str]) -> dict[str, str]:
    """The `--lookup NAME=WHY` rows, or refuse a row with no name or no reason."""
    out: dict[str, str] = {}
    for spec in specs:
        name, _, why = spec.partition("=")
        if not name or not why:
            raise SystemExit(f"--lookup {spec!r}: want NAME=WHY")
        out[name] = why
    return out


def _owner_of(kind: str, owner: str) -> str:
    """The subsystem file `kind` is declared in, or refuse a name outside the convention."""
    name = owner or kind.partition(".")[0]
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise SystemExit(f"--owner {name!r}: lower-case letters, digits and _ only")
    return name


def _row(kind: str, args: argparse.Namespace) -> str:
    """The `[[kind]]` block `kind` declares. Every argument is validated first, so a refusal
    leaves the tree as it was."""
    fields = _fields_arg(args.field)
    text = list(dict.fromkeys(args.text))
    lookup = _lookup_arg(args.lookup)
    if not set(fields) >= {*text, *lookup}:
        raise SystemExit("every --text and --lookup name must be declared with --field first")
    if args.checked and args.exempt:
        raise SystemExit("--checked and --exempt are mutually exclusive")
    if args.exempt and len(args.exempt) <= MIN_REASON:
        raise SystemExit(f"--exempt: say why in more than {MIN_REASON} characters")
    if args.checked and not re.fullmatch(r"[a-z_][a-z0-9_.]*", args.checked):
        raise SystemExit(f"--checked {args.checked!r}: an api function such as items.task_add")
    _owner_of(kind, args.owner)
    why = args.why.strip().rstrip(".") + "."
    if len(why) < MIN_DOC or "|" in why or "\n" in why:
        raise SystemExit(f"--why: one line, no '|', at least {MIN_DOC} characters (the kind's doc)")

    lines = ["[[kind]]", f"name = {_q(kind)}", f"doc = {_q(why)}"]
    if fields:
        fields_s = ", ".join(
            f"{_q(f)} = [{', '.join(_q(t) for t in ts)}]" for f, ts in sorted(fields.items())
        )
        lines.append(f"fields = {{ {fields_s} }}")
    if text:
        lines.append(f"text = [{', '.join(_q(n) for n in text)}]")
    if lookup:
        lookup_s = ", ".join(f"{_q(f)} = {_q(w)}" for f, w in sorted(lookup.items()))
        lines.append(f"lookup = {{ {lookup_s} }}")
    if args.checked:
        lines.append(f"checked = {_q(args.checked)}")
    if args.exempt:
        lines.append(f"exempt = {_q(args.exempt)}")
    lines.append("")
    return "\n".join(lines)


def add(kind: str, args: argparse.Namespace) -> list[str]:
    """Apply the declaration and fragment edits for `kind`; the lines say what was done.
    Every argument is checked before the first file is written, so a refusal leaves the tree
    as it was."""
    block = _row(kind, args)
    by_name = _declared()
    if kind in by_name:
        raise SystemExit(
            f"{kind}: already declared in ddflow/templates/events/{by_name[kind].owner}.toml "
            "(edit its [[kind]] table there or run `check` to see what it still lacks)"
        )
    owner = _owner_of(kind, args.owner)
    reg = EVENTS / f"{owner}.toml"
    if reg.exists():
        reg.write_text(reg.read_text(encoding="utf-8") + "\n" + block, encoding="utf-8")
    else:
        head = f"# Event kinds owned by {owner}. One [[kind]] per kind.\n"
        reg.write_text(head + "\n" + block, encoding="utf-8")
    done = [f"registry: {reg.relative_to(ROOT)}"]
    frag = FRAGMENTS / f"event_kind_added.{kind}.toml"
    if not frag.exists() and not _released(kind):
        why = _q(args.why.strip().rstrip(".") + ".")
        frag.write_text(
            "# One unreleased change; cutting a version folds it into that release.\n\n"
            f'[[change]]\nkind = "event_kind_added"\nkey = {_q(kind)}\nwhy = {why}\n'
            'impact = "additive"\n',
            encoding="utf-8",
        )
        done.append(f"fragment: {frag.relative_to(ROOT)}")
    return done


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="event_kind.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="what each kind still lacks")
    c.add_argument("kinds", nargs="*")
    a = sub.add_parser("add", help="apply the declaration and fragment edits")
    a.add_argument("kind")
    a.add_argument("--why", required=True, help="the fragment's one-line why")
    a.add_argument("--field", action="append", default=[], metavar="NAME=TYPE[,TYPE]")
    a.add_argument("--text", action="append", default=[], metavar="NAME")
    a.add_argument("--lookup", action="append", default=[], metavar="NAME=WHY")
    a.add_argument("--checked", default="", metavar="API.FN")
    a.add_argument("--exempt", default="", metavar="REASON")
    a.add_argument("--owner", default="", metavar="NAME")
    args = p.parse_args(argv)
    if args.cmd == "add":
        for line in add(args.kind, args):
            print(line)
        args = argparse.Namespace(cmd="check", kinds=[args.kind])
    by_name = _declared()
    kinds = args.kinds or sorted(set(HANDLERS) | set(by_name))
    bad = 0
    for kind in kinds:
        for line in lacks(kind, by_name):
            print(f"{kind}: {line}")
            bad += 1
    print("nothing missing" if not bad else f"{bad} edit(s) missing")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
