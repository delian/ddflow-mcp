#!/usr/bin/env python3
"""List or apply the hand edits a new event kind needs (DX-event-kind-scaffold).

    scripts/event_kind.py check [KIND ...]     what each kind still lacks (default: every kind)
    scripts/event_kind.py add KIND --why TEXT [options]

A kind needs six edits, each found otherwise by a failing test, one suite round at a time (the doc of the registry is --why):

  1. handler    `core/model.py` HANDLERS -- code only you can write; `check` reports it
  2. fixture    `tests/fixtures/event_kinds.json`, the kind and its fields' JSON types
  3. redact     `core/redact.py` LOG_TEXT_FIELDS (free text) or LOG_VERBATIM_FIELDS /
                LOG_VERBATIM_NAMES (a lookup) for every str, dict or list field
  4. coherence  `tests/test_coherence_coverage.py` CHECKED or EXEMPT, with a reason
  5. fragment   `ddflow/templates/upgrade/unreleased/event_kind_added.<kind>.toml`
  6. registry   `ddflow/templates/events/<owner>.toml`, a [[kind]] with the kind's doc

`add` writes 2, 3 (as told), 4 (EXEMPT, as told), 5 and 6 and leaves 1 to you; it never edits an
entry that exists. Options (repeatable): --field NAME=TYPE[,TYPE] a field of the payload;
--text NAME a free-text field; --lookup NAME=WHY a field compared or opened;
--exempt REASON why the kind files no checkable text (over 20 characters);
--owner NAME the subsystem file the kind is declared in (default: the kind's first word).

Run from the repository root with the project's environment (`uv run`).
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ddflow.core import eventkinds as EK  # noqa: E402
from ddflow.core.model import HANDLERS  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "event_kinds.json"
REDACT = ROOT / "ddflow" / "core" / "redact.py"
COHERENCE = ROOT / "tests" / "test_coherence_coverage.py"
EVENTS = ROOT / "ddflow" / "templates" / "events"
FRAGMENTS = ROOT / "ddflow" / "templates" / "upgrade" / "unreleased"
MIN_REASON = 20
MIN_DOC = 10
TEXT_TYPES = {"str", "dict", "list"}
TYPES = {"str", "int", "float", "bool", "null", "list", "dict"}


def _coherence() -> tuple[set[str], set[str]]:
    """(CHECKED, EXEMPT) kinds of the coherence test, read from its source (an AST scan: the
    module imports the test fixtures, so it is not imported)."""
    tree = ast.parse(COHERENCE.read_text(encoding="utf-8"))
    out: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") in (
            "CHECKED",
            "EXEMPT",
        ):
            keys: set[str] = set()
            assert isinstance(node.value, ast.Dict)
            for k, v in zip(node.value.keys, node.value.values, strict=True):
                if k is None:  # **dict.fromkeys((kinds...), reason)
                    assert isinstance(v, ast.Call) and isinstance(v.args[0], ast.Tuple)
                    keys |= {e.value for e in v.args[0].elts if isinstance(e, ast.Constant)}
                elif isinstance(k, ast.Constant):
                    keys.add(k.value)
            out[node.target.id] = keys
    return out["CHECKED"], out["EXEMPT"]


def _redact() -> tuple[dict, set, set, set]:
    """(LOG_TEXT_FIELDS, LOG_VERBATIM_FIELDS keys, LOG_VERBATIM_NAMES, LOG_TEXT_NAMES), read
    from the source of `core/redact.py` so that an edit made by `add` is seen at once."""
    want = ("LOG_TEXT_FIELDS", "LOG_VERBATIM_FIELDS", "LOG_VERBATIM_NAMES", "LOG_TEXT_NAMES")
    got: dict = {}
    for node in ast.parse(REDACT.read_text(encoding="utf-8")).body:
        name = getattr(getattr(node, "target", None), "id", "")
        if name in want and isinstance(node, ast.AnnAssign | ast.Assign):
            got[name] = ast.literal_eval(node.value)
        elif isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") in want:
            v = node.value
            got[node.targets[0].id] = ast.literal_eval(v.args[0] if isinstance(v, ast.Call) else v)
    return (
        got["LOG_TEXT_FIELDS"],
        set(got["LOG_VERBATIM_FIELDS"]),
        set(got["LOG_VERBATIM_NAMES"]),
        set(got["LOG_TEXT_NAMES"]),
    )


def lacks(kind: str, snapshot: dict, checked: set[str], exempt: set[str]) -> list[str]:
    """What `kind` still lacks of the five edits, as one line each."""
    out: list[str] = []
    if kind not in HANDLERS:
        out.append("handler: not in core/model.py HANDLERS (write the fold, then register it)")
    entry = snapshot["kinds"].get(kind)
    if entry is None:
        out.append("fixture: no entry in tests/fixtures/event_kinds.json")
        entry = {"fields": {}}
    text_fields, pairs, names, text_names = _redact()
    for name, types in entry["fields"].items():
        known = (
            name in text_fields.get(kind, ())
            or name in names
            or name in text_names
            or (kind, name) in pairs
        )
        if set(types) & TEXT_TYPES and not known:
            out.append(f"redact: field {name!r} is neither LOG_TEXT_FIELDS nor a lookup")
    if kind not in checked and kind not in exempt and not kind.startswith("gate."):
        out.append("coherence: in neither CHECKED nor EXEMPT of tests/test_coherence_coverage.py")
    if kind not in {k.name for k in EK.load_dir(EVENTS)}:
        out.append("registry: declared in no ddflow/templates/events/<owner>.toml")
    if not (FRAGMENTS / f"event_kind_added.{kind}.toml").exists() and not _released(kind):
        out.append(f"fragment: no unreleased/event_kind_added.{kind}.toml")
    return out


def _released(kind: str) -> bool:
    """Whether the shipped manifest already announces `kind`."""
    from ddflow.services import upgrade_manifest as UM

    try:
        return kind in UM.replay(UM.load())[1]
    except Exception:
        return False


def _insert(path: Path, opener: str, lines: list[str]) -> None:
    """Add `lines` right after the line that opens the literal `opener`."""
    text = path.read_text(encoding="utf-8").split("\n")
    at = next((i for i, ln in enumerate(text) if ln.startswith(opener)), None)
    if at is None:
        raise SystemExit(f"{path.name}: no line starts with {opener!r}")
    path.write_text("\n".join([*text[: at + 1], *lines, *text[at + 1 :]]), encoding="utf-8")


def _q(s: str) -> str:
    return json.dumps(s)


def _declare(kind: str, owner: str, why: str) -> list[str]:
    """The registry row and the upgrade fragment of `kind`, each only when absent."""
    done: list[str] = []
    if kind not in {k.name for k in EK.load_dir(EVENTS)}:
        reg = EVENTS / f"{owner}.toml"
        head = "" if reg.exists() else f"# Event kinds owned by {owner}. One [[kind]] per kind.\n"
        row = f"\n[[kind]]\nname = {_q(kind)}\ndoc = {_q(why)}\n"
        reg.write_text((reg.read_text(encoding="utf-8") if reg.exists() else head) + row, "utf-8")
        done.append(f"registry: {reg.relative_to(ROOT)}")
    frag = FRAGMENTS / f"event_kind_added.{kind}.toml"
    if not frag.exists() and not _released(kind):
        frag.write_text(
            "# One unreleased change; cutting a version folds it into that release.\n\n"
            f'[[change]]\nkind = "event_kind_added"\nkey = {_q(kind)}\nwhy = {_q(why)}\n'
            'impact = "additive"\n',
            encoding="utf-8",
        )
        done.append(f"fragment: {frag.relative_to(ROOT)}")
    return done


def add(kind: str, args: argparse.Namespace) -> list[str]:
    """Apply the edits for `kind`; the lines say what was done. Every argument is checked
    before the first file is written, so a refusal leaves the tree as it was."""
    snap = json.loads(FIXTURE.read_text(encoding="utf-8"))
    fields = {}
    for spec in args.field:
        name, _, types = spec.partition("=")
        ts = sorted({t.strip() for t in types.split(",") if t.strip()})
        if not name or not ts or not set(ts) <= TYPES:
            raise SystemExit(f"--field {spec!r}: want NAME=TYPE[,TYPE], types {sorted(TYPES)}")
        fields[name] = ts
    text_fields, taken, names, _ = _redact()
    text = list(dict.fromkeys(n for n in args.text if n not in text_fields.get(kind, ())))
    if text and kind in text_fields:
        raise SystemExit(f"--text: {kind} already has a LOG_TEXT_FIELDS entry; edit it by hand")
    pairs: dict[str, str] = {}
    for spec in args.lookup:
        name, _, why = spec.partition("=")
        if not name or not why:
            raise SystemExit(f"--lookup {spec!r}: want NAME=WHY")
        if (kind, name) not in taken and name not in names:
            pairs.setdefault(name, why)
    checked, exempt = _coherence()
    if args.exempt and kind not in checked | exempt and len(args.exempt) <= MIN_REASON:
        raise SystemExit(f"--exempt: say why in more than {MIN_REASON} characters")
    owner = args.owner or kind.partition(".")[0]
    if not re.fullmatch(r"[a-z][a-z0-9_]*", owner):
        raise SystemExit(f"--owner {owner!r}: lower-case letters, digits and _ only")
    args.why = args.why.strip().rstrip(".") + "."
    if len(args.why) < MIN_DOC or "|" in args.why or "\n" in args.why:
        raise SystemExit(f"--why: one line, no '|', at least {MIN_DOC} characters (the kind's doc)")
    done: list[str] = []
    if kind not in snap["kinds"]:
        snap["kinds"][kind] = {"v": 1, "fields": dict(sorted(fields.items()))}
        snap["kinds"] = dict(sorted(snap["kinds"].items()))
        FIXTURE.write_text(_dump(snap), encoding="utf-8")
        done.append(f"fixture: added {kind} with {len(fields)} field(s)")
    if text:
        _insert(REDACT, "LOG_TEXT_FIELDS:", [f"    {_q(kind)}: ({', '.join(map(_q, text))},),"])
        done.append(f"redact: LOG_TEXT_FIELDS {kind} {text}")
    if pairs:
        _insert(
            REDACT,
            "LOG_VERBATIM_FIELDS:",
            [f"    ({_q(kind)}, {_q(n)}): {_q(w)}," for n, w in pairs.items()],
        )
        done.append(f"redact: {len(pairs)} lookup field(s)")
    if args.exempt and kind not in checked | exempt:
        _insert(COHERENCE, "EXEMPT: dict[str, str] = {", [f"    {_q(kind)}: {_q(args.exempt)},"])
        done.append("coherence: EXEMPT")
    done += _declare(kind, owner, args.why)
    return done


def _dump(snapshot: dict) -> str:
    """The fixture as `tests/test_event_upcasters.py` commits it."""
    text = json.dumps(snapshot, indent=1, sort_keys=True)
    return (
        re.sub(
            r"\[\s*([^\[\]{}]*?)\s*\]",
            lambda m: "[" + ", ".join(x.strip() for x in m.group(1).split(",") if x.strip()) + "]",
            text,
        )
        + "\n"
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="event_kind.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="what each kind still lacks")
    c.add_argument("kinds", nargs="*")
    a = sub.add_parser("add", help="apply the edits that are data")
    a.add_argument("kind")
    a.add_argument("--why", required=True, help="the fragment's one-line why")
    a.add_argument("--field", action="append", default=[], metavar="NAME=TYPE[,TYPE]")
    a.add_argument("--text", action="append", default=[], metavar="NAME")
    a.add_argument("--lookup", action="append", default=[], metavar="NAME=WHY")
    a.add_argument("--exempt", default="", metavar="REASON")
    a.add_argument("--owner", default="", metavar="NAME")
    args = p.parse_args(argv)
    if args.cmd == "add":
        for line in add(args.kind, args):
            print(line)
        args = argparse.Namespace(cmd="check", kinds=[args.kind])
    snap = json.loads(FIXTURE.read_text(encoding="utf-8"))
    checked, exempt = _coherence()
    kinds = args.kinds or sorted(set(HANDLERS) | set(snap["kinds"]))
    bad = 0
    for kind in kinds:
        for line in lacks(kind, snap, checked, exempt):
            print(f"{kind}: {line}")
            bad += 1
    print("nothing missing" if not bad else f"{bad} edit(s) missing")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
