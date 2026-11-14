"""Bf9931aed2b: merging main into a linked worktree must not DROP main's pin-file rows.

The pins merge driver keeps the branch side of a pin file (README.md's generated tables), and
the post-merge hook re-records the stale pins so main's rows come back in. In a linked
worktree that hook run was refused by the *foreign-import* guard -- ``refusing to regenerate
pins: ddflow was imported from the linked worktree`` -- and regenerated nothing, so a knob
row main had added vanished. The refuse-able regenerate route re-records transcript pins by
running pytest (an import from a linked worktree would pin the guard's refusal, not the real
output); the after-merge run re-records only the recipes that allow it, whose regen is not a
transcript suite, so it is safe from the worktree and must regenerate, never refuse.
"""

from __future__ import annotations

from ddflow.api import pin_files as PF
from ddflow.core import outcome as O
from ddflow.services import install_info as I


def test_the_post_merge_hook_in_a_linked_worktree_regenerates_not_refuses(
    repo, tmp_path, monkeypatch
):
    ran: list = []
    monkeypatch.setattr(
        PF.PM, "after_merge", lambda _repo, _cfg: ran.append(_repo) or "regenerated"
    )
    monkeypatch.setattr(
        PF.I,
        "foreign_launch_root",
        lambda: I.ForeignLaunch(tmp_path / "wt", tmp_path / "primary"),
    )
    out = PF.pins_regenerate(repo, start=repo, agent="me", after_merge=True)
    assert out.exit == O.OK, out
    assert "refusing to regenerate pins" not in (out.reason or ""), out
    # the after-merge run re-recorded the pins, it did not silently skip them (Bf9931aed2b)
    assert ran == [repo], ran
    assert "regenerated" in out.data.get("note", ""), out


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
