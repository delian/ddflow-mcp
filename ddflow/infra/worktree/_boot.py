"""Boot-id detection: has the machine rebooted since ddflow last looked?

``/proc/sys/kernel/random/boot_id`` changes once per boot. ddflow stores the last value it
saw in the machine-local ``.ddflow/local/boot`` file, so the first pass after a reboot can
tell -- on its own, without git or the log -- that every pid it recorded earlier is dead.

Linux only. Where ``/proc`` is absent nothing is recorded, ``boot_id()`` returns "" and
``observe_boot`` reports ``changed`` False rather than "no reboot": unknown is not no, and
the one error this detector must never make is to report a reboot from an unreadable marker
and license a post-reboot sweep that nothing caused (orphan-recovery.md research row e).
"""

from __future__ import annotations

from pathlib import Path

#: The kernel rewrites this once per boot; every pid recorded before it is dead after it.
BOOT_ID_PROC = "/proc/sys/kernel/random/boot_id"


def boot_id() -> str:
    """The current boot id, or "" when it cannot be read (not Linux, no /proc)."""
    try:
        return Path(BOOT_ID_PROC).read_text("utf-8").strip()
    except (OSError, UnicodeError):
        return ""


def boot_marker(repo: Path | str) -> Path:
    """Where the last-seen boot id is stored: ``.ddflow/local/boot`` (machine-local)."""
    return Path(repo) / ".ddflow" / "local" / "boot"


def stored_boot(repo: Path | str) -> str:
    """The boot id ddflow last saw for ``repo``, or "" when none was recorded."""
    try:
        return boot_marker(repo).read_text("utf-8").strip()
    except (OSError, UnicodeError):
        return ""


def observe_boot(repo: Path | str) -> dict[str, object]:
    """Record the current boot id and say whether it CHANGED since ddflow last looked.

    Returns ``{"boot_id": cur, "previous": stored, "changed": bool}``. The marker is written
    only when the id really changed (a first sighting counts as a change, from nothing). A
    read OR write failure clears ``changed`` -- never a fabricated "no reboot", and never a
    "rebooted" over an unreadable previous state.
    """
    cur = boot_id()
    if not cur:
        return {"boot_id": "", "previous": stored_boot(repo), "changed": False}
    m = boot_marker(repo)
    try:
        prev = m.read_text("utf-8").strip()
    except FileNotFoundError:
        prev = ""  # never seen this machine: the one change from nothing
    except (OSError, UnicodeError):
        # The marker is present but cannot be read: its previous value is unknown -- never
        # a reboot from it. Still RECORD the current id when the write works, so a corrupt
        # or unreadable marker is replaced rather than stuck forever; report no change
        # (unknown is not no).
        try:
            m.parent.mkdir(parents=True, exist_ok=True)
            m.write_text(cur + "\n")
        except OSError:
            pass
        return {"boot_id": cur, "previous": "", "changed": False}
    if cur != prev:
        try:
            m.parent.mkdir(parents=True, exist_ok=True)
            m.write_text(cur + "\n")
        except OSError:
            return {"boot_id": cur, "previous": prev, "changed": False}
    return {"boot_id": cur, "previous": prev, "changed": cur != prev}
