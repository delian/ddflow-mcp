"""The linked-worktree pins skip note names the remedy (B-pins-worktree-skip-note-names-no-remedy).

Merging main into a task worktree skips the post-merge pin hook; the note said only that it
was skipped, so an agent left its stale pins and spent a failed ci run finding the regressions.
It must name `ddflow pins regenerate`, the one step that brings the worktree's pins current.
"""

from __future__ import annotations

from ddflow.api import pin_files as PF


def test_the_linked_worktree_skip_note_names_the_regenerate_command(repo, monkeypatch):
    monkeypatch.setattr(PF, "_linked_worktree", lambda _r: True)
    out = PF.pins_regenerate(repo, start=repo, agent="me", after_merge=True)
    assert out.exit == 0 and "skipped" in out.data["note"], out
    assert "ddflow pins regenerate" in out.data["note"], out.data["note"]
