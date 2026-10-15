"""D-compat 2 for the `[docs]` section: a config written by a newer ddflow still loads.

An older ddflow that has no `[docs]` section skips the whole table with a note (the unknown-
section path, pinned below with a section no release has). This ddflow, reading a `[docs]`
a NEWER release extended, applies what it knows and notes the rest.
"""

from ddflow.config import Config

EXTRACT = '[[docs.extract]]\nkind = "flags"\npattern = "(--x)"\nsource_globs = ["a/*.py"]\n'


def _load(tmp_path, text):
    (tmp_path / ".ddflow").mkdir(exist_ok=True)
    (tmp_path / ".ddflow" / "config.toml").write_text(text)
    return Config.load(tmp_path)


def test_a_section_this_ddflow_does_not_know_is_skipped_with_a_note(tmp_path):
    cfg = _load(tmp_path, EXTRACT.replace("docs", "docs_from_the_future") + "[lease]\nttl_s = 99\n")
    assert cfg.lease.ttl_s == 99
    assert any("docs_from_the_future" in n for n in cfg.unknown_knobs)


def test_a_newer_extractor_kind_or_field_is_skipped_with_a_note(tmp_path):
    newer = (
        EXTRACT
        + '[[docs.extract]]\nkind = "widgets"\npattern = "(a)"\nsource_globs = ["a"]\n'
        + '[[docs.extract]]\nkind = "tools"\npattern = "(t)"\nsource_globs = ["a"]\nfuture = 1\n'
        + "[docs]\nfuture_knob = 3\n"
    )
    cfg = _load(tmp_path, newer)
    assert [e["kind"] for e in cfg.docs.extract] == ["flags", "tools"]
    assert "future" not in cfg.docs.extract[1]
    notes = " ".join(cfg.ignored_members + cfg.unknown_knobs)
    assert "widgets" in notes and "future" in notes and "future_knob" in notes
