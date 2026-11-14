"""Bf9931aed2b: merging main into a linked worktree must not DROP main's pin-file rows.

The pins merge driver keeps the branch side of a pin file (README.md's generated tables), and
the post-merge hook re-records the stale pins so main's rows come back in. In a linked
worktree that hook run was refused by the *foreign-import* guard -- ``refusing to regenerate
pins: ddflow was imported from the linked worktree`` -- and regenerated nothing, so a knob
row main had added vanished. The refusable regenerate route re-records transcript pins by
running pytest (an import from a linked worktree would pin the guard's refusal, not the real
output); the after-merge run re-records only the recipes that allow it, whose regen is not a
transcript suite, so it is safe from the worktree and must not refuse there.
"""

from __future__ import annotations

from pathlib import Path

from ddflow.api import pin_files as PF
from ddflow.core import outcome as O
from ddflow.services import install_info as I


def test_the_post_merge_hook_in_a_linked_worktree_is_not_refused_as_a_foreign_import(
    repo, tmp_path, monkeypatch
):
    monkeypatch.setattr(PF, "_linked_worktree", lambda _r: True)
    monkeypatch.setattr(
        PF.I,
        "foreign_launch_root",
        lambda: I.ForeignLaunch(tmp_path / "wt", tmp_path / "primary"),
    )
    out = PF.pins_regenerate(repo, start=repo, agent="me", after_merge=True)
    assert out.exit == O.OK, out
    assert "refusing to regenerate pins" not in (out.reason or ""), out
    note = out.data.get("note", "")
    assert "post-merge hook skipped in a linked worktree" in note, out
    # Bf9931aed2b: the remedy must point at the primary checkout -- the interactive route
    # refuses from a linked worktree, so "run pins regenerate" alone fails in the reported env.
    assert "ddflow pins regenerate` from the primary checkout" in note, note


def test_an_interactive_regenerate_from_a_foreign_import_still_refuses(repo, tmp_path, monkeypatch):
    """The guard still protects the route that re-records transcript pins: a bare
    `pins regenerate` (not the after-merge run) from another tree refuses, never re-records
    the import guard's refusal into a pin."""
    monkeypatch.setattr(
        PF.I,
        "foreign_launch_root",
        lambda: I.ForeignLaunch(tmp_path / "wt", tmp_path / "primary"),
    )
    out = PF.pins_regenerate(repo, start=repo, agent="me")
    assert out.exit == O.REFUSED, out
    assert "refusing to regenerate pins" in (out.reason or ""), out
