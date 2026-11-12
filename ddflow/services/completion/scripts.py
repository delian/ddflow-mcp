"""The shell activation scripts `ddflow completion <bash|zsh|fish>` serves.

One template per shell under `ddflow/templates/completion/`; each only registers the ONE
completion engine (argcomplete over the argparse parser `build_parser()` returns) and
spells no command, option or choice name. Without argcomplete the script prints a clear
message and returns normally -- never a broken prompt (a silent broken prompt is unbeatable
when every prompt keystroke fails).

Each template stays under 20 KB (a file this module reads and serves verbatim).
"""

from __future__ import annotations

from pathlib import Path

SHELLS = ("bash", "zsh", "fish")
_MAX_BYTES = 20 * 1024

_TEMPLATE = Path(__file__).resolve().parent.parent.parent / "templates" / "completion"


def for_shell(shell: str) -> str:
    """The activation script for ``shell`` (one of `SHELLS`), or raise ValueError."""
    if shell not in SHELLS:
        raise ValueError(f"completion shell {shell!r} is not one of {SHELLS}")
    path = _TEMPLATE / shell
    if not path.is_file() or path.stat().st_size > _MAX_BYTES:
        raise AssertionError(f"completion template {shell!r} missing or over {_MAX_BYTES} bytes")
    return path.read_text("utf-8")
