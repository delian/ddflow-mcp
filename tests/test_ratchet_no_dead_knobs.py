"""Ratchet: every documented configuration knob must actually be READ by the code.

A knob an operator can set that nothing consumes is a lie in the documentation, and it
is invisible to every other test — the config loads, the value is stored, and nothing
happens. This is the dead-knob class, and it is mechanically detectable, so it ships as
a check rather than as a rule someone has to remember.

Found four dead knobs on first run (`gates.unavailable_is_failure`,
`lessons.max_results`, `lessons.cadence_growth_pct`, `lessons.cadence_min_entries`);
all four were wired up rather than deleted, because each described behaviour that was
genuinely intended.

The allowlist below may only SHRINK.
"""

from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from orchard.config import Config

#: Knobs the NAME-BASED scan cannot see. Each needs a written reason, and the list may
#: only SHRINK — `test_the_allowlist_only_shrinks` fails on a stale entry.
KNOWN_UNREAD: dict[str, str] = {
    # Read dynamically: `cli._prompt_overrides` iterates `dataclasses.fields(cfg.prompts)`
    # and pulls every non-empty value, so no source line ever spells `.prompts.<name>`.
    # Verified live by `test_prompt_override_from_config_is_honoured`, which sets each
    # one and asserts the template actually changes — a stronger check than the grep.
    "prompts.review_system": "read dynamically via dataclasses.fields in _prompt_overrides",
    "prompts.review_user": "read dynamically via dataclasses.fields in _prompt_overrides",
    "prompts.gate_instruction": "read dynamically via dataclasses.fields in _prompt_overrides",
    "prompts.session_brief_header": "read dynamically via dataclasses.fields in _prompt_overrides",
}


def _sources() -> str:
    pkg = pathlib.Path(__file__).resolve().parents[1] / "orchard"
    return "\n".join(p.read_text("utf-8") for p in pkg.glob("*.py") if p.name != "config.py")


def _alias_map(src: str) -> dict[str, str]:
    """Local names bound to a config section: ``lc = cfg.loops`` -> {"lc": "loops"}.

    Resolving these keeps the detector strong. The alternative -- allowlisting every
    knob read through an alias -- would grow the exemption list for a pattern that is
    perfectly good style, and an exemption list that grows for good reasons stops being
    read for the bad ones.
    """
    out: dict[str, str] = {}
    for m in re.finditer(r"^\s*(\w+)\s*=\s*(?:self\.)?(?:c\.)?cfg\.(\w+)\s*$", src, re.M):
        out[m.group(1)] = m.group(2)
    return out


def dead_knobs(keys: list[str] | None = None, src: str | None = None) -> list[str]:
    """Which of ``keys`` never appear in ``src``.

    Both inputs are injectable so the detector itself can be tested against planted
    input. A scan whose inputs can only come from the live tree is a scan that cannot
    be shown to work — it passes identically when it has silently stopped looking.
    """
    src = _sources() if src is None else src
    keys = [k for k, *_ in Config.load().explain()] if keys is None else keys
    aliases = _alias_map(src)
    dead = []
    for key in keys:
        section, knob = key.split(".", 1)
        if re.search(rf"\.{re.escape(section)}\.{re.escape(knob)}\b", src):
            continue
        # ...or through a local alias of that section.
        if any(
            re.search(rf"\b{re.escape(alias)}\.{re.escape(knob)}\b", src)
            for alias, sec in aliases.items()
            if sec == section
        ):
            continue
        dead.append(key)
    return dead


def test_no_knob_is_dead():
    dead = [k for k in dead_knobs() if k not in KNOWN_UNREAD]
    assert not dead, (
        f"these knobs are documented but never read — either wire them up or delete them: {dead}"
    )


def test_the_allowlist_only_shrinks():
    stale = [k for k in KNOWN_UNREAD if k not in dead_knobs()]
    assert not stale, f"allowlisted knobs that are now read; remove them: {stale}"


def test_the_detector_resolves_section_aliases():
    """`lc = cfg.loops` then `lc.max_gate_flaps` is a read, and must not be reported."""
    src = "lc = cfg.loops\nif flips < lc.max_gate_flaps: pass"
    assert dead_knobs(["loops.max_gate_flaps"], src) == []
    assert dead_knobs(["loops.max_reopens"], src) == ["loops.max_reopens"]


def test_the_detector_can_still_see():
    """Planted-bad-input checks: a detector that cannot fail is not a detector."""
    # A knob that is definitely absent must be reported...
    assert dead_knobs(["lease.definitely_unreferenced_xyzzy"], "nothing here") == [
        "lease.definitely_unreferenced_xyzzy"
    ]
    # ...and one that is present must not be.
    assert dead_knobs(["lease.ttl_s"], "x = cfg.lease.ttl_s") == []


def test_the_scan_is_not_vacuous():
    """Positive harvest: the real enumeration must actually find knobs to check."""
    keys = [k for k, *_ in Config.load().explain()]
    assert len(keys) > 20, f"only {len(keys)} knobs enumerated; did the scan break?"
    assert _sources().count("cfg.") > 20, "the source scan returned almost nothing"
