"""This machine's name, in the two forms ddflow stamps and compares (D-unify).

`hostname()` is the name the OS reports: the full form a wait or a job records, and the one
a redactor must scrub. `short_host()` is its first label: the form an actor or an agent id
carries. A record stamped in one form and read in the other is the same machine, so every
comparison goes through `same_host`, never `==`.
"""

from __future__ import annotations

import socket


def hostname() -> str:
    """The machine's name as the OS reports it ("" when it cannot say)."""
    try:
        return socket.gethostname()
    except OSError:
        return ""


def short_host() -> str:
    """The first label of `hostname()` (``box`` for ``box.example.org``)."""
    return hostname().split(".")[0]


def same_host(recorded: str, here: str | None = None) -> bool:
    """Is ``recorded`` (a name stamped by any ddflow, full or short) this machine?

    Two full names are equal or different machines; a short name stands for the first
    label of a full one, so ``box`` is ``box.example.org``. ``here`` defaults to this
    machine; an empty ``recorded`` names no machine and is not "elsewhere".
    """
    if not recorded:
        return True
    mine = hostname() if here is None else here
    if recorded == mine:
        return True
    if "." in recorded and "." in mine:
        return False
    return recorded.split(".", maxsplit=1)[0] == mine.split(".")[0]
