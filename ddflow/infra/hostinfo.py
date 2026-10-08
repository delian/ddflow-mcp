"""This machine's name, in the two forms ddflow stamps and compares (D-unify).

`hostname()` is the name the OS reports: the full form a wait or a job records, and the one
a redactor must scrub. `short_host()` is its first label: the form an actor or an agent id
carries. Waits and jobs record the full form and compare it through `same_host`, so the
form is decided once.
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
    """Is ``recorded`` (the name a wait or a job stamped) this machine?

    Exactly the name this machine reports: a short name is NOT bridged to a full one,
    because two machines of one site can report ``node1`` and ``node1.example.org``, and
    "elsewhere" is the safe answer for a record on a shared checkout (its pid means
    nothing here). ``here`` defaults to this machine; an empty ``recorded`` names no
    machine and is not "elsewhere".
    """
    return not recorded or recorded == (hostname() if here is None else here)
