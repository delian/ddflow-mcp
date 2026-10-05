"""Bf3566bbacd: doctor called a KNOWN enum knob with a bad file value an "unknown config
key". Since B-enum-strict-fallback the key is known and its strictest value is in effect;
only the value is wrong, and doctor says so. A key that really is unknown keeps the old
wording."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli


def test_a_bad_enum_value_is_an_invalid_value_not_an_unknown_key(repo):
    run_cli(repo, "init")
    local = repo / ".ddflow" / "local"
    local.mkdir(exist_ok=True)
    (local / "config.toml").write_text(
        '[enforce]\nstale_docs = "blok"\n[lease]\nbogus_knob = 1\n"foo = bar" = 1\n', "utf-8"
    )
    _code, out, err = run_cli(repo, "doctor")
    text = out + err
    assert "unknown config key enforce.stale_docs" not in text, text
    assert "invalid value for enforce.stale_docs = 'blok'" in text, text
    assert "in effect: 'block'" in text, text
    assert "unknown config key lease.bogus_knob" in text, text
    # An unknown key spelled like a value is still an unknown key (roborev on 183bcf03).
    assert "unknown config key lease.foo = bar" in text, text
