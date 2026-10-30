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
from ddflow.config import Config

#: Knobs the NAME-BASED scan cannot see. Each needs a written reason, and the list may
#: only SHRINK — `test_the_allowlist_only_shrinks` fails on a stale entry.
KNOWN_UNREAD: dict[str, str] = {
    # Read by a shell script, not by Python: scripts/ci/pre-push asks `ddflow config --filter
    # log.pre_push_events` (tests/test_events_committed_doors.py drives all three modes).
    "log.pre_push_events": "read by scripts/ci/pre-push through `ddflow config`",
    # Read dynamically: `cli._prompt_overrides` iterates `dataclasses.fields(cfg.prompts)`
    # and pulls every non-empty value, so no source line ever spells `.prompts.<name>`.
    # Verified live by `test_prompt_override_from_config_is_honoured`, which sets each
    # one and asserts the template actually changes — a stronger check than the grep.
    "prompts.review_system": "read dynamically via dataclasses.fields in _prompt_overrides",
    "prompts.review_user": "read dynamically via dataclasses.fields in _prompt_overrides",
    "prompts.gate_instruction": "read dynamically via dataclasses.fields in _prompt_overrides",
    "prompts.session_brief_header": "read dynamically via dataclasses.fields in _prompt_overrides",
    "prompts.agent_brief": "read dynamically via dataclasses.fields in overrides_from",
    "prompts.agent_report": "read dynamically via dataclasses.fields in overrides_from",
}


def _file_sources() -> list[str]:
    pkg = pathlib.Path(__file__).resolve().parents[1] / "ddflow"
    # `rglob`, not `glob`: the package is layered (core/ infra/ services/ views/
    # surfaces/) and a non-recursive scan sees only `__init__.py` and `config.py` —
    # which reports every knob in the package as dead. `test_the_scan_is_not_vacuous`
    # below is what caught that, and is why it exists. One entry per file, because a
    # local alias names a section within that file only (see `_aliases`).
    return [
        p.read_text("utf-8")
        for p in pkg.rglob("*.py")
        # The config's own definitions are not reads: config.py and its sections package.
        if p.name != "config.py"
        and p.relative_to(pkg).parts[0] != "config_sections"
        and "__pycache__" not in str(p)
    ]


def _sources() -> str:
    return "\n".join(_file_sources())


#: A name bound to a config section: `lc = cfg.loops` -> "loops". `(?![\w.])` keeps a
#: deeper path from binding: `agent=cfg.agent.id` must NOT bind "agent", or the scan
#: quietly weakens for every `agent.*` knob.
_ALIAS_RE = re.compile(r"(\w+)\s*=\s*(?:self\.)?(?:[\w]+\.)?cfg\.(\w+)(?![\w.])")


def _aliases(src: str) -> dict[str, str]:
    """Names bound to a config section in ONE file: ``lc = cfg.loops`` -> {"lc": "loops"}.

    Resolving a local name keeps the detector strong. The alternative — allowlisting every
    knob read through an alias — would grow the exemption list for a pattern that is
    perfectly good style, and an exemption list that grows for good reasons stops being
    read for the bad ones.
    """
    return {m.group(1): m.group(2) for m in _ALIAS_RE.finditer(src)}


def _unique_aliases(per_file: list[dict[str, str]]) -> dict[str, str]:
    """Names bound to exactly ONE section across the whole tree.

    A section carried under a name across files — a parameter like ``session`` or an
    attribute like ``log_cfg`` — is read in a file that never binds it to ``cfg.<that
    section>``, so that name is unambiguous and its reads are matched anywhere. A name
    bound to SEVERAL sections (``s = cfg.schedule`` in one file, ``s = cfg.swarm`` in
    another) is a local short name and must resolve per file, so it is omitted here.
    """
    sections: dict[str, set[str]] = {}
    for aliases in per_file:
        for name, sec in aliases.items():
            sections.setdefault(name, set()).add(sec)
    return {name: next(iter(secs)) for name, secs in sections.items() if len(secs) == 1}


def dead_knobs(keys: list[str] | None = None, src: str | list[str] | None = None) -> list[str]:
    """Which of ``keys`` never appear in ``src``.

    Both inputs are injectable so the detector itself can be tested against planted
    input. A scan whose inputs can only come from the live tree is a scan that cannot
    be shown to work — it passes identically when it has silently stopped looking.
    ``src`` is one file's text; pass a list of strings to plant several files.
    """
    if src is None:
        files = _file_sources()
    elif isinstance(src, str):
        files = [src]
    else:
        files = list(src)
    keys = [k for k, *_ in Config.load().explain()] if keys is None else keys
    per_file = [_aliases(text) for text in files]
    dead = []
    for key in keys:
        section, knob = key.split(".", 1)
        if _read_in_any(section, knob, files, per_file, _unique_aliases(per_file)):
            continue
        dead.append(key)
    return dead


def _read_in_any(
    section: str,
    knob: str,
    files: list[str],
    per_file: list[dict[str, str]],
    unique: dict[str, str],
) -> bool:
    """Whether ``section.knob`` is read in ``files``, directly or through an alias.

    A local alias is resolved within the file that bound it; a name unambiguous across
    the tree is matched in any file (``_unique_aliases``). One match anywhere is enough.
    """
    direct = rf"\.{re.escape(section)}\.{re.escape(knob)}\b"
    for text, aliases in zip(files, per_file, strict=True):
        if re.search(direct, text):
            return True
        if any(
            sec == section and re.search(rf"\b{re.escape(alias)}\.{re.escape(knob)}\b", text)
            for alias, sec in aliases.items()
        ):
            return True
    return any(
        sec == section
        and any(re.search(rf"\b{re.escape(name)}\.{re.escape(knob)}\b", t) for t in files)
        for name, sec in unique.items()
    )


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


def test_the_alias_map_is_scoped_to_one_file():
    """A local alias lives in ONE file, so `s = cfg.swarm` elsewhere must not steal `s`
    from `s = cfg.schedule` and hide the schedule.* reads behind the swarm binding."""
    src = [
        "s = cfg.schedule\nif age > s.max_age_s: pass\n",
        "s = cfg.swarm\nif w > s.window_s: pass\n",
    ]
    assert dead_knobs(["schedule.max_age_s", "swarm.window_s"], src) == []


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


#: A selected `ddflow ci run` runs this file for EVERY item (B7d4ab0b268): its input is the whole
#: code base, so no import closure can know a change breaks it.
ALWAYS = True
