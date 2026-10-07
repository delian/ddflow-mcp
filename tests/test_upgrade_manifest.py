"""The shipped upgrade manifest (B-upgrade.2-changes.1-manifest).

The manifest's base plus every release replays to exactly the knobs and event kinds of the
code that ships it. So a knob added, a default flipped or an event kind added without an
entry fails HERE, on the branch that made the change, rather than reaching an operator's
project unannounced (the dedupe warn -> ask flip in 0.1.10 did).
"""

from __future__ import annotations

import importlib.util
import re

import pytest

from ddflow.core.events import version_key
from ddflow.services import upgrade_manifest as UM

FIX = "uv run python scripts/upgrade_manifest.py backfill"


def test_replaying_the_manifest_gives_this_releases_knobs_and_event_kinds():
    knobs, kinds = UM.replay(UM.load())
    here = UM.knob_defaults()
    missing = sorted(k for k in here if k not in knobs)
    changed = sorted(k for k in here if k in knobs and knobs[k] != here[k])
    gone = sorted(k for k in knobs if k not in here)
    assert not (missing or changed or gone), (
        f"config knobs the upgrade manifest does not announce -- added: {missing}, "
        f"default changed: {changed}, removed: {gone}. Run `{FIX}` and commit "
        f"ddflow/templates/upgrade/ (one fragment per change under unreleased/); add a "
        f"`why` and, for a changed default, the `effect` a project will see."
    )
    new_kinds, lost = sorted(UM.event_kinds() - kinds), sorted(kinds - UM.event_kinds())
    assert not (new_kinds or lost), (
        f"event kinds the upgrade manifest does not announce -- added: {new_kinds}, "
        f"removed: {lost}. Run `{FIX}` and commit ddflow/templates/upgrade/."
    )


def test_every_knob_entry_says_why_and_every_changed_default_its_effect():
    changes = UM.load().changes()
    cut = [
        f"{c.version} {c.key}: {c.why}"
        for c in changes
        if re.search(r"\b(e\.g|i\.e)\.$", c.why) or c.why.count("(") != c.why.count(")")
    ]
    assert not cut, f"a why cut mid-sentence: {cut}"
    blank = [
        f"{c.version} {c.kind} {c.key}: " + ("effect" if c.why else "why")
        for c in changes
        if c.kind.startswith("knob_") and (not c.why or (c.kind == "knob_changed" and not c.effect))
    ]
    assert not blank, (
        f"upgrade manifest entries a project cannot act on: {blank}. Write the `why` (one "
        f"line) and, for a changed default, the `effect` a project that never set the knob "
        f"will see, in ddflow/templates/upgrade/."
    )


def _script():
    spec = importlib.util.spec_from_file_location(
        "um_script", UM.MANIFEST.parents[3] / "scripts" / "upgrade_manifest.py"
    )
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    return script


def test_a_cut_version_takes_over_the_unreleased_entries():
    """Each fragment goes to the release whose commit first holds its file, with its
    hand-written fields; one no release holds stays unreleased (roborev, critic)."""
    script = _script()
    flip = {"kind": "knob_changed", "key": "x.y", "effect": "e", "impact": "breaking"}
    feature = {"kind": "feature", "key": "f"}
    later = {"kind": "knob_added", "key": "z", "why": "hand-written"}
    mine = {
        "by_key": {("knob_changed", "x.y"): flip, ("knob_added", "z"): later},
        "manual": [feature],
    }
    kept = {"0.2.0": {"by_key": {}, "manual": []}, UM.UNRELEASED: mine}
    holds = {("0.2.2", "knob_changed.x.y.toml"), ("0.2.2", "feature.f.toml")}
    two = script.assign_unreleased(kept, ["0.2.0", "0.2.1", "0.2.2"], lambda v, n: (v, n) in holds)
    assert two["0.2.2"] == {"by_key": {("knob_changed", "x.y"): flip}, "manual": [feature]}
    assert "0.2.1" not in two
    assert two[UM.UNRELEASED] == {"by_key": {("knob_added", "z"): later}, "manual": []}
    assert script.assign_unreleased(kept, ["0.2.0"], lambda v, n: True)[UM.UNRELEASED] == mine
    a = {"knobs": {"x.y": 1}, "event_kinds": [], "docs": {}}
    b = {"knobs": {"x.y": 2}, "event_kinds": [], "docs": {"x.y": "Doc."}}
    (e,) = script.diff("0.2.2", a, b, two["0.2.2"]["by_key"])
    assert (e["effect"], e["impact"]) == ("e", "breaking")


def test_holds_asks_git_and_never_reads_could_not_tell_as_absent():
    script = _script()
    assert script.holds("HEAD", "ddflow/templates/upgrade/changes.toml")
    assert not script.holds("HEAD", "ddflow/templates/upgrade/unreleased/no-such.toml")
    with pytest.raises(SystemExit, match="git cannot list"):
        script.holds("0" * 40, "ddflow/templates/upgrade/changes.toml")


def test_the_why_is_the_docs_first_sentence_even_with_an_abbreviation():
    doc = "Files bumped, as a regex (e.g. `0.1.2`). The rest."
    assert _script()._why(doc) == "Files bumped, as a regex (e.g. `0.1.2`)."


def test_changes_since_the_base_name_every_knob_that_differs_from_it():
    m = UM.load()
    here = UM.knob_defaults()
    differs = {k for k, v in here.items() if m.base_knobs.get(k, object()) != v}
    named = {c.key for c in UM.changes_since(m.base_version, m) if c.kind.startswith("knob_")}
    assert differs <= named
    assert len(differs) >= 60  # measured 0.1.3 -> 0.2.0: 63 added, 7 defaults changed


def test_changes_since_a_version_are_the_newer_releases_oldest_first():
    m = UM.load()
    since = UM.changes_since("0.1.9", m)
    assert all(UM.release_key(c.version) > UM.release_key("0.1.9") for c in since)
    versions = [c.version for c in since]
    assert versions == sorted(versions, key=UM.release_key)
    flip = [c for c in since if c.key == "dedupe.on_match"]
    assert [(c.version, c.old, c.new) for c in flip] == [("0.1.10", "warn", "ask")]
    assert "refused" in flip[0].effect
    assert UM.changes_since("not a version", m) == m.changes()
    released = [v for v, _d, _c in m.releases if v != UM.UNRELEASED]
    assert all(c.version == UM.UNRELEASED for c in UM.changes_since(released[-1], m))


def test_every_release_is_listed_oldest_first_after_the_base():
    m = UM.load()
    assert m.base_version == "0.1.3"
    keys = [UM.release_key(v) for v, _d, _c in m.releases]
    assert keys == sorted(keys) and keys[0] > UM.release_key(m.base_version)
    assert all(version_key(v) for v, _d, _c in m.releases if v != UM.UNRELEASED)


BASE = """schema_version = 1
[base]
version = "0.1.0"
event_kinds = ["a.b"]
[base.knobs]
"x.y" = '"old"'
"""


def test_a_fragment_is_an_unreleased_change_and_replays():
    rel = BASE + '[[release]]\nversion = "0.1.1"\n'
    frag = '[[change]]\nkind = "knob_changed"\nkey = "x.y"\nold = \'"old"\'\nnew = \'"new"\'\n'
    kind = '[[change]]\nkind = "event_kind_added"\nkey = "c.d"\n'
    m = UM.parse(rel, [("knob.x.y.toml", frag), ("kind.c.d.toml", kind)])
    assert [v for v, _d, _c in m.releases] == ["0.1.1", UM.UNRELEASED]
    assert UM.replay(m) == ({"x.y": "new"}, {"a.b", "c.d"})
    assert UM.replay(m, upto="0.1.1") == ({"x.y": "old"}, {"a.b"})
    assert [c.key for c in UM.changes_since("0.1.1", m)] == ["c.d", "x.y"]


def test_a_null_default_round_trips():
    frag = '[[change]]\nkind = "knob_added"\nkey = "x.z"\nnew = \'null\'\n'
    (c,) = UM.parse(BASE, [("f.toml", frag)]).changes()
    assert c.new is None and c.has_new and not c.has_old


@pytest.mark.parametrize(
    ("body", "why"),
    [
        (
            '[[release]]\nversion = "0.1.1"\n[[release.change]]\nkind = "knob_twiddled"\nkey = "x"\n',
            "kind",
        ),
        (
            '[[release]]\nversion = "0.1.1"\n[[release.change]]\nkind = "knob_added"\nkey = "x"\n',
            "new default",
        ),
        (
            '[[release]]\nversion = "0.1.1"\n[[release.change]]\nkind = "knob_removed"\nkey = "x"\n',
            "old default",
        ),
        (
            '[[release]]\nversion = "0.1.1"\n[[release.change]]\nkind = "feature"\nkey = "x"\nimpact = "huge"\n',
            "impact",
        ),
        (
            '[[release]]\nversion = "0.1.1"\n[[release.change]]\nkind = "feature"\nkey = "x"\nwho = "me"\n',
            "unknown field",
        ),
        ('[[release]]\nversion = "0.1.2"\n[[release]]\nversion = "0.1.1"\n', "oldest first"),
        ('[[release]]\nversion = "0.0.9"\n', "oldest first"),
        ('[[release]]\nversion = "0.1.1"\n[[release]]\nversion = "0.1.1"\n', "twice"),
        ('[[release]]\nversion = "soon"\n', "not a release"),
        ('[release]\nversion = "0.1.1"\n', "array of tables"),
        (
            '[[release]]\nversion = "0.1.1"\n[release.change]\nkind = "feature"\nkey = "x"\n',
            "array of tables",
        ),
        (
            '[[release]]\nversion = "0.1.1"\n[[release.change]]\nkind = "knob_added"\nkey = "x"\nnew = "ask"\n',
            "not JSON",
        ),
    ],
)
def test_a_manifest_that_breaks_the_schema_is_refused(body, why):
    with pytest.raises(UM.ManifestError, match=why):
        UM.parse(BASE + body)


def test_a_release_that_is_not_a_table_is_refused():
    with pytest.raises(UM.ManifestError, match="not a table"):
        UM.parse('release = ["0.1.1"]\n' + BASE)
    for bad in ('"x"', '""', "[]", "0", "false"):
        with pytest.raises(UM.ManifestError, match=r"\[base.knobs\] is a table"):
            UM.parse(f'schema_version = 1\n[base]\nversion = "0.1.0"\nknobs = {bad}\n')
    with pytest.raises(UM.ManifestError, match="not a release"):
        UM.parse('schema_version = 1\n[base]\nversion = "main"\n[[release]]\nversion = "0.2.0"\n')
    with pytest.raises(UM.ManifestError, match="not a release"):
        UM.replay(UM.load(), upto="main")
    for bad in ('""', "0", "[1]"):
        with pytest.raises(UM.ManifestError, match="event_kinds"):
            UM.parse(f'schema_version = 1\n[base]\nversion = "0.1.0"\nevent_kinds = {bad}\n')
    with pytest.raises(UM.ManifestError, match="array of tables"):
        UM.parse('release = ""\n' + BASE)


def test_the_manifest_ships_inside_the_package():
    assert UM.MANIFEST.is_file()
    assert UM.MANIFEST.parent.parent.name == "templates"
    assert UM.MANIFEST.parents[2].name == "ddflow"
