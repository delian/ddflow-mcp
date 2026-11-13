"""A ticket's note is written IN PLACE: the ticket file is the one its owner holds the flock on,
so a rename over it would leave the owner locking an inode nobody else opens."""

from __future__ import annotations

import os

from ddflow.infra import fsio
from ddflow.services import suitequeue as SQ


def test_the_note_is_written_into_the_same_inode_the_lock_is_held_on(tmp_path) -> None:
    ticket = tmp_path / "ticket"
    ticket.write_text("")
    before = os.stat(ticket).st_ino
    with fsio.file_lock(ticket, 1.0):
        SQ.write_note(ticket, "phase", 12.5, "agent-a")
    assert os.stat(ticket).st_ino == before
    assert SQ.read_note(ticket) == {"class": "phase", "cost_s": 12.5, "agent": "agent-a"}


def test_write_in_place_truncates_and_keeps_the_inode(tmp_path) -> None:
    p = tmp_path / "f"
    p.write_text("a long first body")
    ino = os.stat(p).st_ino
    fsio.write_in_place(p, "short")
    assert p.read_text() == "short"
    assert os.stat(p).st_ino == ino
