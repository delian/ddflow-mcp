"""The release lint (B-upgrade.2-changes.2-lint, decision D-upgrade-manifest-lint).

A knob or event kind the code changed and the manifest does not announce BLOCKS a release by
default; the block names the changes, the entry that would announce each and the operator's
options; a waiver is recorded; `[release].manifest_lint` = warn | off relaxes it.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from conftest import run_cli

from ddflow import api
from ddflow import config as C
from ddflow.services import upgrade_manifest as UM
from ddflow.surfaces import cli

OK, REFUSED = 0, 3


@pytest.fixture
def tree(repo: Path, monkeypatch) -> Path:
    """A repo that ships its own copy of the manifest, as ddflow's source tree does, and
    code with one knob and one event kind the manifest has never heard of."""
    dest = repo / "ddflow" / "templates" / "upgrade"
    shutil.copytree(UM.MANIFEST.parent, dest)
    monkeypatch.setattr(UM, "MANIFEST", dest / "changes.toml")
    monkeypatch.setattr(UM, "WAIVERS", dest / "waivers.toml")
    return repo


@pytest.fixture
def drift(monkeypatch):
    real_knobs, real_kinds = UM.knob_defaults, UM.event_kinds
    monkeypatch.setattr(UM, "knob_defaults", lambda cfg=None: {**real_knobs(cfg), "x.new_knob": 3})
    monkeypatch.setattr(UM, "event_kinds", lambda: real_kinds() | {"x.thing_happened"})


def test_this_tree_is_clean():
    res = UM.lint()
    assert res.clean, UM.report(res)


def test_a_knob_and_an_event_kind_with_no_entry_are_found_by_name(drift):
    res = UM.lint()
    assert [u.id for u in res.unmanifested] == [
        "knob_added:x.new_knob",
        "event_kind_added:x.thing_happened",
    ]


def test_a_flipped_default_and_a_removed_knob_are_found(monkeypatch):
    real = UM.knob_defaults()
    gone = next(k for k in real if k.startswith("lease."))
    flipped = next(k for k in real if k.startswith("review.") and isinstance(real[k], int))
    changed = {k: v for k, v in real.items() if k != gone} | {flipped: real[flipped] + 1}
    res = UM.lint(knobs=changed)
    ids = {u.id: u for u in res.unmanifested}
    assert set(ids) == {f"knob_removed:{gone}", f"knob_changed:{flipped}"}
    assert ids[f"knob_changed:{flipped}"].old == real[flipped]
    assert ids[f"knob_changed:{flipped}"].new == real[flipped] + 1


def test_the_report_lists_the_three_options_and_a_prefilled_entry_that_parses(drift):
    res = UM.lint()
    text = UM.report(res)
    assert "knob_added:x.new_knob" in text and "event_kind_added:x.thing_happened" in text
    assert "--waive <change> --reason" in text and "release.manifest_lint" in text
    assert "unreleased/knob_added.x.new_knob.toml" in text
    knob = next(u for u in res.unmanifested if u.kind == "knob_added")
    # the pre-filled fragment is a valid manifest entry, so replaying it clears the finding
    m = UM.parse(UM.MANIFEST.read_text("utf-8"), [("f.toml", knob.fragment())])
    assert UM.replay(m)[0]["x.new_knob"] == 3


def test_a_waiver_is_recorded_and_moves_the_change_out_of_the_block(tree, drift):
    w = UM.waive("x.new_knob", "internal, no project can set it", result=UM.lint())
    assert w.change == "knob_added:x.new_knob"
    assert UM.load_waivers() == [w]
    res = UM.lint()
    assert [u.id for u in res.unmanifested] == ["event_kind_added:x.thing_happened"]
    assert [(u.id, v.reason) for u, v in res.waived] == [
        ("knob_added:x.new_knob", "internal, no project can set it")
    ]


def test_a_waiver_needs_a_reason_and_a_change_that_is_unmanifested(tree, drift):
    with pytest.raises(UM.ManifestError, match="reason"):
        UM.waive("x.new_knob", "  ")
    with pytest.raises(UM.ManifestError, match="not one unmanifested change"):
        UM.waive("lease.ttl_s", "because")
    assert not UM.WAIVERS.exists()


def test_block_refuses_with_the_options_warn_says_and_off_is_silent(tree, drift):
    out = api.version_lint(tree)
    assert out.exit == REFUSED and "Options (the operator decides)" in out.reason
    assert out.data["unmanifested"][0]["change"] == "knob_added:x.new_knob"
    for policy, exit_, warned in (("warn", OK, True), ("off", OK, False)):
        code, _o, _e = run_cli(tree, "config", "release.manifest_lint", policy, "--local")
        assert code == OK
        out = api.version_lint(tree)
        assert out.exit == exit_ and bool(out.data["warning"]) is warned


def test_version_cut_is_blocked_by_the_lint_before_it_tags(tree, drift):
    out = api.version_cut(tree, bump="patch", dry_run=True)
    assert out.exit == REFUSED and "knob_added:x.new_knob" in out.reason


def test_a_project_that_does_not_ship_the_manifest_is_not_linted(repo, drift):
    out = api.version_lint(repo)
    assert out.exit == OK and out.data["unmanifested"] == []


def _cli(tree: Path, *argv: str) -> int:
    """In process: the drift is patched into this interpreter, not a subprocess's."""
    return cli.main(["--repo", str(tree), *argv])


def test_cli_lint_exits_3_and_json_names_the_changes(tree, drift, capsys):
    assert _cli(tree, "version", "lint") == REFUSED
    assert "knob_added:x.new_knob" in capsys.readouterr().err
    assert _cli(tree, "--json", "version", "lint") == REFUSED
    assert "knob_added:x.new_knob" in json.dumps(json.loads(capsys.readouterr().out))


def test_cli_waive_records_it_and_unblocks_what_it_names(tree, drift):
    for ch in ("knob_added:x.new_knob", "event_kind_added:x.thing_happened"):
        assert _cli(tree, "version", "lint", "--waive", ch, "--reason", "test") in (OK, REFUSED)
    assert _cli(tree, "version", "lint") == OK
    assert "knob_added:x.new_knob" in UM.WAIVERS.read_text("utf-8")


def test_the_policy_knob_is_a_strict_enum():
    assert C.KNOB_CHOICES["release.manifest_lint"] == ("block", "warn", "off")
    assert C.KNOB_STRICTEST["release.manifest_lint"][0] == "block"
    assert C.Config().release.manifest_lint == "block"


def test_a_waiver_covers_that_change_only_not_the_knobs_next_one(tree, monkeypatch):
    real = UM.knob_defaults()
    key = next(k for k in real if k.startswith("review.") and isinstance(real[k], int))
    first = {**real, key: real[key] + 1}
    UM.waive(key, "this release only", result=UM.lint(knobs=first))
    assert UM.lint(knobs=first).clean
    again = UM.lint(knobs={**real, key: real[key] + 2})
    assert [u.id for u in again.unmanifested] == [f"knob_changed:{key}"]


def test_a_lint_that_cannot_run_stops_the_cut_only_under_block(tree, monkeypatch):
    UM.MANIFEST.write_text("not [valid toml", "utf-8")
    assert api.version_lint(tree).exit == REFUSED
    assert api.version_cut(tree, bump="patch", dry_run=True).exit == REFUSED
    assert run_cli(tree, "config", "release.manifest_lint", "warn", "--local")[0] == OK
    cut = api.version_cut(tree, bump="patch", dry_run=True)
    assert cut.data["unavailable"] and "could not run" in cut.data["warning"]
    for policy in ("warn", "off"):
        assert run_cli(tree, "config", "release.manifest_lint", policy, "--local")[0] == OK
        out = api.version_lint(tree)
        assert out.exit == OK
        assert ("could not run" in out.data["warning"]) is (policy == "warn")
        assert bool(out.data.get("unavailable")) is (policy == "warn")


def test_a_ddflow_installed_inside_the_project_is_not_the_projects_manifest(
    repo, drift, monkeypatch
):
    site = repo / ".venv" / "lib" / "python3" / "site-packages" / "ddflow" / "templates" / "upgrade"
    shutil.copytree(UM.MANIFEST.parent, site)
    monkeypatch.setattr(UM, "MANIFEST", site / "changes.toml")
    out = api.version_lint(repo)
    assert out.exit == OK and out.data["unmanifested"] == []
