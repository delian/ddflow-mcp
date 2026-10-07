"""The shipped upgrade manifest: what changed in each release, for a project upgrading.

`ddflow/templates/upgrade/changes.toml` ships in the wheel (B-upgrade.2-changes). It holds
a BASE -- every config knob's default and every event kind as of the oldest release it
covers -- and, per release, the changes since the one before: knobs added, changed or
removed (old and new default, and why), event kinds added or removed, and the entries no
code diff can find on its own -- instruction, hook or template refreshes, data repairs and
opt-in features with the command that enables each.

Replaying the base and every release reproduces the knobs and event kinds of the code that
ships the manifest, exactly; a test holds that, so a knob added or a default flipped with no
entry fails before it ships (the release lint, B-upgrade.2-changes.2-lint, is the same
comparison at release time). `scripts/upgrade_manifest.py` backfilled the history from the
release commits and writes the entries for a new change.

A change not yet in a release is a FRAGMENT: one small file per change under
`templates/upgrade/unreleased/` (`<kind>.<key>.toml`, holding `[[change]]` tables), read as
the `unreleased` release. One file per change, so two branches that each add a knob never
edit the same file; cutting a version folds the fragments into its release block.

A default is stored JSON-encoded (`'"ask"'`, `'4'`, `'["bug"]'`), because TOML has no null
and a knob's value can be a table; `Change.old` and `Change.new` are the decoded values.

The release lint (D-upgrade-manifest-lint) is `lint()`: the replay compared with the code
as it stands, a change with no entry reported by name with the entry that would announce
it, and `waive()` the operator's recorded exception (`waivers.toml`, beside the manifest).
Everything but `waive()` is pure over the files: it reads them, never writes them.
"""

from __future__ import annotations

import dataclasses
import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import config as C
from ..core.events import version_key
from ..core.model import HANDLERS
from ..infra.paths import templates_dir

#: Where the manifest ships, inside the package.
MANIFEST = templates_dir() / "upgrade" / "changes.toml"

#: The release a change on main belongs to until a version is cut for it.
UNRELEASED = "unreleased"

#: Every `kind` a change may have.
KINDS = (
    "knob_added",
    "knob_changed",
    "knob_removed",
    "event_kind_added",
    "event_kind_removed",
    "refresh",
    "repair",
    "feature",
)

#: The impact classes of the compatibility contract (docs/ddflow/compatibility.md).
IMPACTS = ("additive", "deprecating", "breaking")

_MISSING = object()


class ManifestError(ValueError):
    """The manifest does not parse, or an entry breaks its schema."""


@dataclass(frozen=True)
class Change:
    """One entry: what changed in ``version``.

    ``key`` is the knob (``section.knob``), event kind, refreshed file, repair id or feature
    name. ``old``/``new`` are decoded defaults (a knob's), ``None`` where they do not apply
    -- see ``has_old``/``has_new`` for a default that IS null. ``why`` is one line;
    ``effect`` what a project that never set the knob will see; ``impact`` the contract
    class when declared; ``enable`` the command that turns an opt-in feature on."""

    version: str
    kind: str
    key: str
    why: str = ""
    effect: str = ""
    impact: str = ""
    enable: str = ""
    old: Any = None
    new: Any = None
    has_old: bool = False
    has_new: bool = False


@dataclass
class Manifest:
    """The parsed file: the base snapshot and every release's changes, oldest first."""

    base_version: str
    base_knobs: dict[str, Any]
    base_event_kinds: list[str]
    releases: list[tuple[str, str, list[Change]]] = field(default_factory=list)

    def changes(self) -> list[Change]:
        return [c for _v, _d, cs in self.releases for c in cs]


def release_key(version: str) -> tuple:
    """Order releases: by version, with `unreleased` after every released one."""
    return (1, ()) if version == UNRELEASED else (0, version_key(version))


def _decode(raw: Any, where: str) -> Any:
    if not isinstance(raw, str):
        raise ManifestError(f"{where}: a default is stored JSON-encoded as a string")
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise ManifestError(f"{where}: not JSON ({exc})") from exc


def _change(version: str, raw: Any, where: str) -> Change:
    if not isinstance(raw, dict):
        raise ManifestError(f"{where}: a change is a table")
    kind, key = raw.get("kind", ""), raw.get("key", "")
    if kind not in KINDS:
        raise ManifestError(f"{where}: kind {kind!r} is not one of {', '.join(KINDS)}")
    if not isinstance(key, str) or not key:
        raise ManifestError(f"{where}: a change names its key")
    impact = raw.get("impact", "")
    if impact and impact not in IMPACTS:
        raise ManifestError(f"{where}: impact {impact!r} is not one of {', '.join(IMPACTS)}")
    unknown = set(raw) - {"kind", "key", "why", "effect", "impact", "enable", "old", "new"}
    if unknown:
        raise ManifestError(f"{where}: unknown field(s) {', '.join(sorted(unknown))}")
    old = _decode(raw["old"], f"{where} old") if "old" in raw else _MISSING
    new = _decode(raw["new"], f"{where} new") if "new" in raw else _MISSING
    if kind in ("knob_added", "knob_changed") and new is _MISSING:
        raise ManifestError(f"{where}: {kind} needs the new default")
    if kind in ("knob_changed", "knob_removed") and old is _MISSING:
        raise ManifestError(f"{where}: {kind} needs the old default")
    return Change(
        version=version,
        kind=kind,
        key=key,
        why=str(raw.get("why", "")),
        effect=str(raw.get("effect", "")),
        impact=str(impact),
        enable=str(raw.get("enable", "")),
        old=None if old is _MISSING else old,
        new=None if new is _MISSING else new,
        has_old=old is not _MISSING,
        has_new=new is not _MISSING,
    )


#: The directory of unreleased fragments, beside the manifest.
FRAGMENTS = "unreleased"


def _toml(text: str, what: str) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"{what} is not TOML: {exc}") from exc


def _releases(data: dict[str, Any]) -> list[tuple[str, str, list[Change]]]:
    out: list[tuple[str, str, list[Change]]] = []
    seen: set[str] = set()
    rels = data.get("release", [])
    if not isinstance(rels, list):
        raise ManifestError("`release` is an array of tables: write [[release]], not [release]")
    for i, rel in enumerate(rels):
        if not isinstance(rel, dict):
            raise ManifestError(f"release #{i + 1} is not a table")
        version = str(rel.get("version", ""))
        if not version or (version != UNRELEASED and not version_key(version)):
            raise ManifestError(f"release #{i + 1}: version {version!r} is not a release")
        if version in seen:
            raise ManifestError(f"release {version} is listed twice")
        seen.add(version)
        cs = [
            _change(version, c, f"release {version} change #{j + 1}")
            for j, c in enumerate(_tables(rel.get("change"), f"release {version}"))
        ]
        out.append((version, str(rel.get("date", "")), cs))
    return out


def _tables(raw: Any, where: str) -> list[Any]:
    """An array of `[[change]]` tables (each checked by `_change`)."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ManifestError(f"{where}: `change` is an array of tables: write [[...change]]")
    return raw


def _fragments(fragments: list[tuple[str, str]]) -> list[Change]:
    return [
        _change(UNRELEASED, c, f"unreleased fragment {name} change #{j + 1}")
        for name, body in sorted(fragments)
        for j, c in enumerate(_tables(_toml(body, f"fragment {name}").get("change"), name))
    ]


def parse(text: str, fragments: list[tuple[str, str]] | None = None) -> Manifest:
    """The manifest in ``text``, with the unreleased ``fragments`` ((name, text) pairs)
    as its `unreleased` release; ManifestError when either breaks the schema."""
    data = _toml(text, "the manifest")
    if data.get("schema_version") != 1:
        raise ManifestError(f"schema_version {data.get('schema_version')!r}: this reads 1")
    base = data.get("base")
    if not isinstance(base, dict) or not base.get("version"):
        raise ManifestError("the manifest has no [base] with a version")
    if not isinstance(base["version"], str) or not version_key(base["version"]):
        raise ManifestError(f"[base] version {base['version']!r} is not a release")
    raw_knobs = base.get("knobs", {})
    if not isinstance(raw_knobs, dict):
        raise ManifestError("[base.knobs] is a table of JSON-encoded defaults")
    knobs = {k: _decode(v, f"base.knobs.{k}") for k, v in raw_knobs.items()}
    kinds = base.get("event_kinds", [])
    if not isinstance(kinds, list) or not all(isinstance(k, str) for k in kinds):
        raise ManifestError("[base] event_kinds is an array of event kind names")
    out = Manifest(str(base["version"]), knobs, sorted(kinds))
    out.releases = _releases(data)
    loose = _fragments(fragments or [])
    if loose and out.releases and out.releases[-1][0] == UNRELEASED:
        out.releases[-1][2].extend(loose)
    elif loose:
        out.releases.append((UNRELEASED, "", loose))
    order = [release_key(v) for v, _d, _c in out.releases]
    if order != sorted(order) or (order and order[0] <= release_key(out.base_version)):
        raise ManifestError("releases must be listed oldest first, all newer than the base")
    return out


def load(path: Path | str | None = None) -> Manifest:
    """The shipped manifest (or the one at ``path``) with the unreleased fragments in the
    `unreleased/` directory beside it."""
    p = Path(path or MANIFEST)
    frags = sorted((p.parent / FRAGMENTS).glob("*.toml"))
    return parse(p.read_text("utf-8"), [(f.name, f.read_text("utf-8")) for f in frags])


def changes_since(version: str, manifest: Manifest | None = None) -> list[Change]:
    """Every change in a release NEWER than ``version``, oldest release first: what a
    project last worked on by ``version`` meets when it upgrades. An unparsable
    ``version`` (none recorded) is older than everything: every change is returned.

    The base is a floor: a project older than ``base_version`` gets every listed change,
    but not what changed before the base (its knobs are in ``replay(upto=base)``)."""
    m = manifest or load()
    since = release_key(version) if version_key(version) or version == UNRELEASED else (0, ())
    return [c for v, _d, cs in m.releases if release_key(v) > since for c in cs]


def replay(manifest: Manifest | None = None, upto: str = "") -> tuple[dict[str, Any], set[str]]:
    """The knob defaults and event kinds the manifest says a release has: the base with
    every change up to and including ``upto`` (default: all of them) applied."""
    if upto and upto != UNRELEASED and not version_key(upto):
        raise ManifestError(f"replay upto {upto!r}: not a release version")
    m = manifest or load()
    knobs, kinds = dict(m.base_knobs), set(m.base_event_kinds)
    limit = release_key(upto) if upto else None
    for v, _d, cs in m.releases:
        if limit is not None and release_key(v) > limit:
            break
        for c in cs:
            if c.kind in ("knob_added", "knob_changed"):
                knobs[c.key] = c.new
            elif c.kind == "knob_removed":
                knobs.pop(c.key, None)
            elif c.kind == "event_kind_added":
                kinds.add(c.key)
            elif c.kind == "event_kind_removed":
                kinds.discard(c.key)
    return knobs, kinds


def knob_defaults(cfg: Any = None) -> dict[str, Any]:
    """Every knob's default as plain JSON data, keyed ``section.knob``: what the
    manifest records. (`scripts/upgrade_manifest.py` computes the same for a release's
    tree in a subprocess; trees older than this module cannot import it.)"""
    cfg = cfg if cfg is not None else C.Config()
    skip = set(getattr(C, "_NOT_SECTIONS", ()))
    out: dict[str, Any] = {}
    for f in dataclasses.fields(cfg):
        sec = getattr(cfg, f.name)
        if f.name in skip or not dataclasses.is_dataclass(sec):
            continue
        for g in dataclasses.fields(sec):
            out[f"{f.name}.{g.name}"] = json.loads(json.dumps(getattr(sec, g.name), default=str))
    return out


def event_kinds() -> set[str]:
    """The event vocabulary of the running code (`core.model.HANDLERS`)."""
    return set(HANDLERS)


#: The operator's recorded waivers, beside the manifest; absent until the first is written.
WAIVERS = MANIFEST.parent / "waivers.toml"

#: What `[release].manifest_lint` accepts.
LINT_POLICIES = ("block", "warn", "off")

_WHY_CHARS = 160


@dataclass(frozen=True)
class Unmanifested:
    """A difference between the code and the manifest's replay: a change nobody announced.

    ``kind`` is a `KINDS` member the code can find on its own (a knob or event kind added,
    changed or removed); ``old`` and ``new`` the replay's value and the code's."""

    kind: str
    key: str
    old: Any = None
    new: Any = None
    has_old: bool = False
    has_new: bool = False
    why: str = ""

    @property
    def id(self) -> str:
        """The name `--waive` takes: ``<kind>:<key>``."""
        return f"{self.kind}:{self.key}"

    def line(self) -> str:
        if self.kind == "knob_added":
            return f"{self.id}  (new default {json.dumps(self.new)})"
        if self.kind == "knob_changed":
            return f"{self.id}  (default {json.dumps(self.old)} -> {json.dumps(self.new)})"
        if self.kind == "knob_removed":
            return f"{self.id}  (was {json.dumps(self.old)})"
        return self.id

    def fragment(self) -> str:
        """The unreleased fragment (`unreleased/<kind>.<key>.toml`) that would announce it,
        pre-filled from the diff; the operator-facing `why`/`effect` are the author's to
        refine."""
        out = ["[[change]]", f"kind = {json.dumps(self.kind)}", f"key = {json.dumps(self.key)}"]
        if self.has_old:
            out.append(f"old = {json.dumps(json.dumps(self.old, sort_keys=True))}")
        if self.has_new:
            out.append(f"new = {json.dumps(json.dumps(self.new, sort_keys=True))}")
        out.append(f"why = {json.dumps(self.why or 'TODO: one line, why')}")
        if self.kind == "knob_changed":
            out.append('effect = "TODO: what a project that never set it will see"')
        if self.kind in ("knob_added", "event_kind_added"):
            out.append('impact = "additive"')
        return "\n".join(out) + "\n"

    @property
    def fragment_name(self) -> str:
        return f"{FRAGMENTS}/{self.kind}.{self.key}.toml"


@dataclass(frozen=True)
class Waiver:
    """One change the operator let through unmanifested, and why."""

    change: str
    reason: str
    version: str = ""


@dataclass
class LintResult:
    """What `lint()` found: the changes with no entry and the ones a waiver covers."""

    unmanifested: list[Unmanifested] = field(default_factory=list)
    waived: list[tuple[Unmanifested, Waiver]] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.unmanifested


def _first_sentence(doc: str) -> str:
    first = doc.strip().replace("e.g.", "e\0g\0").replace("i.e.", "i\0e\0").split(". ", 1)[0]
    first = first.replace("\0", ".")
    return first if len(first) <= _WHY_CHARS else first[: _WHY_CHARS - 3].rstrip(" .") + "..."


def load_waivers(path: Path | str | None = None) -> list[Waiver]:
    """The recorded waivers; none when the file does not exist."""
    p = Path(path or WAIVERS)
    if not p.exists():
        return []
    rows = _toml(p.read_text("utf-8"), "the waivers file").get("waiver", [])
    out = []
    for i, w in enumerate(_tables(rows, "waivers")):
        if not isinstance(w, dict) or not w.get("change") or not w.get("reason"):
            raise ManifestError(f"waiver #{i + 1}: a waiver names its change and its reason")
        out.append(Waiver(str(w["change"]), str(w["reason"]), str(w.get("version", ""))))
    return out


def lint(
    manifest: Manifest | None = None,
    *,
    waivers: list[Waiver] | None = None,
    knobs: dict[str, Any] | None = None,
    kinds: set[str] | None = None,
    docs: dict[str, str] | None = None,
) -> LintResult:
    """Compare the code's knob defaults and event kinds (``knobs``/``kinds``, default: the
    running code's) with the manifest's replay; whatever differs is a change nobody
    announced. A change a waiver names moves to ``waived``."""
    m = manifest or load()
    have_k, have_e = (
        knob_defaults() if knobs is None else knobs,
        event_kinds() if kinds is None else kinds,
    )
    said_k, said_e = replay(m)
    notes = C.KNOB_DOCS if docs is None else docs
    found: list[Unmanifested] = []
    for k in sorted(have_k):
        why = _first_sentence(notes.get(k, ""))
        if k not in said_k:
            found.append(Unmanifested("knob_added", k, new=have_k[k], has_new=True, why=why))
        elif said_k[k] != have_k[k]:
            found.append(
                Unmanifested(
                    "knob_changed", k, said_k[k], have_k[k], has_old=True, has_new=True, why=why
                )
            )
    found += [
        Unmanifested("knob_removed", k, old=said_k[k], has_old=True)
        for k in sorted(set(said_k) - set(have_k))
    ]
    found += [Unmanifested("event_kind_added", k) for k in sorted(have_e - said_e)]
    found += [Unmanifested("event_kind_removed", k) for k in sorted(said_e - have_e)]
    by_change = {w.change: w for w in (load_waivers() if waivers is None else waivers)}
    out = LintResult()
    for u in found:
        (
            out.waived.append((u, by_change[u.id]))
            if u.id in by_change
            else out.unmanifested.append(u)
        )
    return out


def waive(
    change: str, reason: str, *, path: Path | str | None = None, result: LintResult | None = None
) -> Waiver:
    """Record that ``change`` (``<kind>:<key>``, or a bare key naming exactly one) may
    ship with no manifest entry, for ``reason``. ManifestError when it is not one of the
    changes `lint()` reports, or ``reason`` is blank: a waiver is a decision with a why."""
    if not reason.strip():
        raise ManifestError("a waiver needs a reason (--reason)")
    res = result or lint()
    hits = [u for u in res.unmanifested if change in (u.id, u.key)]
    if len(hits) != 1:
        names = ", ".join(u.id for u in res.unmanifested) or "none"
        raise ManifestError(
            f"{change!r} is not one unmanifested change to waive (unmanifested now: {names})"
        )
    target = Path(path or WAIVERS)
    w = Waiver(hits[0].id, " ".join(reason.split()))
    head = (
        ""
        if target.exists()
        else "# Changes the operator let a release ship with no manifest entry.\n"
    )
    with target.open("a", encoding="utf-8") as fh:
        fh.write(
            f"{head}\n[[waiver]]\nchange = {json.dumps(w.change)}\nreason = {json.dumps(w.reason)}\n"
        )
    return w


def report(res: LintResult) -> str:
    """The block message: what is unmanifested and the operator's three options."""
    out = [
        f"release manifest lint: {len(res.unmanifested)} change(s) the upgrade manifest does "
        "not announce (a project upgrading would meet them unannounced):"
    ]
    out += [f"  {u.line()}" for u in res.unmanifested]
    out += [
        "",
        "Options (the operator decides):",
        "  1. Have an agent write the entries and the upgrade repairs or notes a SAFE upgrade "
        "needs, then re-run `ddflow version lint`. Pre-filled from the diff, one fragment "
        "file each under ddflow/templates/upgrade/:",
    ]
    for u in res.unmanifested:
        out += [
            f"       {u.fragment_name}",
            *("         " + ln for ln in u.fragment().splitlines()),
        ]
    out += [
        '  2. Waive a change for this release: `ddflow version lint --waive <change> --reason "<why>"` '
        "(recorded in ddflow/templates/upgrade/waivers.toml and shown in the next upgrade plan).",
        "  3. Change the policy: `ddflow config release.manifest_lint warn|off [--local]`.",
    ]
    return "\n".join(out)
