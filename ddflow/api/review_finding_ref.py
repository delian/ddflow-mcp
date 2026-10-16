"""Name a finding for `review triage` by its number (#N) or by its digest.

`ddflow review show` prints both; triage used to take only the number and answered a
digest with a bare "invalid int" (bug B8b2e487a13).
"""

from __future__ import annotations

import re

#: A digest prefix shorter than this is too easy to hit by accident.
MIN_PREFIX = 6

_HEX = re.compile(r"[0-9a-f]+")


def resolve(ref, found: list[dict], where: str) -> tuple[int, str]:
    """The 1-based number of the finding ``ref`` names, or ``(0, why)``.

    ``ref`` is a number (int or "3" or "#3") or a unique prefix, at least MIN_PREFIX hex
    characters, of one finding's digest. ``found`` is the gate's recorded findings.
    """
    text = str(ref if ref is not None else "").strip().lstrip("#").lower()
    listing = "\n".join(
        f"  #{n} digest {f.get('digest', '')} [{f.get('severity', '')}] {f.get('title', '')}"
        for n, f in enumerate(found, 1)
    )
    forms = (
        "a number (#N) or a unique digest prefix of at least "
        f"{MIN_PREFIX} hex characters; the findings of {where}:\n{listing}"
    )
    if text.isdigit() and 1 <= int(text) <= len(found):
        return int(text), ""
    if len(text) >= MIN_PREFIX and _HEX.fullmatch(text):
        hits = [n for n, f in enumerate(found, 1) if str(f.get("digest", "")).startswith(text)]
        if len(hits) == 1:
            return hits[0], ""
        if hits:
            return (
                0,
                f"{text!r} matches {len(hits)} findings (#{', #'.join(map(str, hits))}): give more of the digest or the number. Name {forms}",
            )
        return 0, f"no finding of {where} has digest {text!r}. Name {forms}"
    if not text:
        return 0, f"say which finding. Name {forms}"
    if text.isdigit():
        return 0, f"no finding #{text}: {where} has #1..#{len(found)}. Name {forms}"
    return 0, f"{text!r} is no finding of {where} (#1..#{len(found)}). Name {forms}"
