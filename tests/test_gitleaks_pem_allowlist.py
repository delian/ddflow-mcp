"""The `.gitleaks.toml` allowlist for the placeholder PEM block in the redaction test.

`tests/test_critic_findings.py` builds a PEM block to prove the redactor removes a whole block;
the gitleaks `private-key` rule reads it as a key. The allowlist entry must excuse exactly that
block in exactly that file: the checks below pin the scope of THAT entry (one rule, one anchored
path, one anchored block) by running its own regex over the text the scanner reads (the Python
source of the three adjacent string literals) and over variants. Another entry is only checked for
excusing the placeholder body in every file (no `paths` and a regex that matches it).
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_FILE = "tests/test_critic_findings.py"


def _applies_to_private_key(a: dict) -> bool:
    """An allowlist with no `targetRules` applies to every rule; one that lists it, to it."""
    return "targetRules" not in a or "private-key" in a["targetRules"]


def _entry() -> dict:
    """The one allowlist entry that covers the fixture file for `private-key`: it applies to
    that rule (no `targetRules`, or listing it) and has a `paths` regex matching the file. It
    is asserted to be the single such entry, and to name that rule alone."""
    cfg = tomllib.loads((ROOT / ".gitleaks.toml").read_text("utf-8"))
    hits = [
        a
        for a in cfg["allowlists"]
        if _applies_to_private_key(a)
        and any(re.search(p, FIXTURE_FILE) for p in a.get("paths", []))
    ]
    assert len(hits) == 1, "exactly one allowlist entry covers the fixture file for private-key"
    assert hits[0].get("targetRules") == ["private-key"], "and it names that one rule only"
    # a path-free entry that can apply to private-key applies to EVERY file: its regexes must not
    # match the placeholder body (that would excuse the body everywhere)
    for a in cfg["allowlists"]:
        if a is not hits[0] and _applies_to_private_key(a) and not a.get("paths"):
            assert not any(
                re.search(rx, "MIIEowIBAAKCAQEAsecretbodyhere") for rx in a.get("regexes", [])
            ), f"a path-free allowlist excuses the placeholder body in every file: {a}"
    return hits[0]


def _scanner_secret() -> str:
    """What the private-key rule captures in the fixture file: the source text from the BEGIN
    frame to the END frame, quotes and `\\n` joins of the adjacent literals included."""
    src = (ROOT / FIXTURE_FILE).read_text("utf-8")
    m = re.search(r"-----BEGIN RSA PRIVATE KEY-----[\s\S-]{64,}?KEY-----", src)
    assert m, "the placeholder block is gone: drop the allowlist entry with it"
    return m.group(0)


def test_the_entry_is_scoped_to_one_rule_and_one_file():
    e = _entry()
    assert e["condition"] == "AND"
    assert len(e["paths"]) == 1 and re.search(e["paths"][0], FIXTURE_FILE)
    assert not re.search(e["paths"][0], "tests/other.py")
    assert not re.search(e["paths"][0], "ddflow/" + FIXTURE_FILE)
    assert not re.search(e["paths"][0], FIXTURE_FILE + ".bak")
    assert len(e["regexes"]) == 1


def test_the_regex_excuses_the_placeholder_block_as_the_scanner_reads_it():
    (rx,) = _entry()["regexes"]
    assert re.search(rx, _scanner_secret())


def test_the_regex_excuses_no_other_block():
    (rx,) = _entry()["regexes"]
    secret = _scanner_secret()
    assert not re.search(rx, secret.replace("secretbodyhere", "secretbodyhereMORE"))
    assert not re.search(rx, secret.replace("MIIEowIBAAKCAQEA", "MIIEowIBAAKCAQEB"))
    assert not re.search(rx, "x" + secret)
    assert not re.search(rx, secret + "x")


def test_the_placeholder_body_is_not_key_material():
    """The proof the entry's comment claims: the body is a short filler, spelling itself."""
    body = "MIIEowIBAAKCAQEAsecretbodyhere"
    assert len(body) == 30 and body.endswith("secretbodyhere")
    assert len(body) < 100  # an RSA private key body is over a thousand base64 characters
