"""Slugs, safe file names and anchors: the one place a string becomes an identifier-ish
name (B-uni-textkit.2-slug, D-unify).

Seven private helpers each re-implemented "make this text safe for a name", with rules that
differ on purpose (which characters survive, what replaces the rest, case, length, what an
empty result becomes). They are parameters of the functions here, not copies, and every
caller states its rule once, so changing a rule changes it where it is used. Pure, stdlib
only. Anchors keep GitHub's rule; a Claude Code project directory keeps Claude Code's.
"""

from __future__ import annotations

import re
import string

_ASCII_ALNUM = frozenset(string.ascii_letters + string.digits)


def safe_filename(
    s: str,
    *,
    max: int | None = None,
    repl: str = "-",
    keep: str = "._-",
    unicode: bool = False,
    strip: str = "",
    default: str = "",
) -> str:
    """``s`` with every character outside ``[A-Za-z0-9]`` + ``keep`` replaced by ``repl``
    (``""`` drops it); then, in this order, stripped of ``strip`` at both ends, cut to ``max``
    (``None``: no cut; the cut can leave a ``strip`` character at the end, as the callers'
    original expressions did), and ``default`` if nothing is left. ``unicode`` lets any
    ``str.isalnum()`` character through, not only ASCII.
    """
    out = "".join(
        c if (c.isalnum() if unicode else c in _ASCII_ALNUM) or c in keep else repl for c in s
    )
    if strip:
        out = out.strip(strip)
    if max is not None:
        out = out[:max]
    return out or default


def slug(text: str, limit: int = 48, default: str = "item") -> str:
    """A lower-case id fragment: word characters kept (Unicode too), runs of space, ``_`` and
    ``-`` become one ``-``, cut to ``limit``, never empty."""
    s = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    s = re.sub(r"[\s_-]+", "-", s)
    return s[:limit].strip("-") or default


def ascii_slug(text: str, limit: int, *, restrip: bool = True) -> str:
    """Lower-case ASCII words joined by ``-``, cut to ``limit`` (may be empty). The cut can
    leave a trailing ``-``: ``restrip`` removes it."""
    out = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()[:limit]
    return out.strip("-") if restrip else out


def github_anchor(heading: str) -> str:
    """GitHub's anchor for a heading: markup stripped, lower-cased, punctuation dropped
    (hyphens and underscores kept), each space a hyphen."""
    h = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    h = re.sub(r"<[^>]+>", "", h)
    h = re.sub(r"[`*~]", "", h).strip().lower()
    h = re.sub(r"[^\w\- ]", "", h)
    return h.replace(" ", "-")


def claude_project_slug(path: object) -> str:
    """Claude Code's directory name for a checkout: every non-alphanumeric byte -> ``-``."""
    return safe_filename(str(path), repl="-", keep="")
