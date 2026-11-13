"""Boot-id detection: has the machine rebooted since ddflow last looked?

``/proc/sys/kernel/random/boot_id`` changes once per boot. ddflow stores the last value it
saw in the machine-local ``.ddflow/local/boot`` file, so the first pass after a reboot can
tell -- on its own, without git or the log -- that every pid it recorded earlier is dead.

Linux only. Where ``/proc`` is absent nothing is recorded, ``boot_id()`` returns "" and
``observe_boot`` reports ``changed`` False rather than "no reboot": unknown is not no, and
the one error this detector must never make is to run a post-reboot sweep that was never
licensed (orphan-recovery.md research row e).
"""

from __future__ import annotations

from pathlib import Path

#: The kernel rewrites this once per boot; every pid recorded before it is dead after it.
BOOT_ID_PROC = "/proc/sys/kernel/random/boot_id"


def boot_id() -> str:
    """The current boot id, or "" when it cannot be read (not Linux, no /proc)."""
    try:
        return Path(BOOT_ID_PROC).read_text("utf-8").strip()
    except OSError:
        return ""


def boot_marker(repo: Path | str) -> Path:
    """Where the last-seen boot id is stored: ``.ddflow/local/boot`` (machine-local)."""
    return Path(repo) / ".ddflow" / "local" / "boot"


def stored_boot(repo: Path | str) -> str:
    """The boot id ddflow last saw for ``repo``, or "" when none was recorded."""
    try:
        return boot_marker(repo).read_text("utf-8").strip()
    except OSError:
        return ""


def observe_boot(repo: Path | str) -> dict[str, object]:
    """Record the current boot id and say whether it CHANGED since ddflow last looked.

    Returns ``{"boot_id": cur, "previous": stored, "changed": bool}``. The marker is written
    only when the id changed (a first sighting counts as a change, from nothing), and the
    change word is the whole output any caller acts on: reading and writing never raise, and
    an unreadable boot id clears ``changed`` — never a fabricated "no reboot".
    """
    cur = boot_id()
    prev = stored_boot(repo)
    if not cur or cur == prev:
        return {"boot_id": cur, "previous": prev, "changed": False}
    changed = False
    try:
        m = boot_marker(repo)
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text(cur + "\n")
        changed = True
    except OSError:
        changed = False
    return {"boot_id": cur, "previous": prev, "changed": changed}
