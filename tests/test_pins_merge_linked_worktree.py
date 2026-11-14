"""Bf9931aed2b: merging main into a linked worktree must not refuse the post-merge pin
hook as a foreign import.

When ``git merge main`` runs in a linked worktree the pins merge driver keeps the branch side
of a pin file, and the post-merge hook is SKIPPED for the worktree (B683c75d7b3) with a note
that names ``ddflow pins regenerate``. The skip note was unreachable there: the
foreign-import guard refused the rose route first -- ``refusing to regenerate pins: ddflow
was imported from the linked worktree`` -- regenerating nothing and telling the agent to run
the command from the primary checkout, so a knob row main added was read as drift and the
merge dropped it. The refuse is for the *interactive* regenerate route (a transcript recipe
would pin the guard's refusal, Bc28808f9c3); the after-merge run re-records only the recipes
that allow it, so it reaches the skip note, never refuses.
"""

from __future__ import annotations

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
    assert "post-merge hook skipped in a linked worktree" in out.data.get("note", ""), out
    assert "ddflow pins regenerate" in out.data.get("note", ""), out


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
