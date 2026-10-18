"""Per-run and per-item overrides of `[review].exclude_globs` (DX-review-exclude-per-item)."""

from types import SimpleNamespace

from ddflow.services import review_exclude as REX

CFG = SimpleNamespace(review=SimpleNamespace(exclude_globs=["*.lock"], exclude_pin_files=True))


def _globs(tags=None, exclude="", include_pinned=False):
    REX.pins.pin_files = lambda repo, cfg: ["golden.ambr"]
    return REX.patterns(CFG, None, tags, exclude, include_pinned)


def test_config_is_the_default():
    assert _globs() == (["*.lock"], ["golden.ambr"])


def test_item_tags_replace_the_config_globs():
    assert _globs(tags=["x", "review-exclude:docs/**"])[0] == ["docs/**"]


def test_the_run_flag_beats_the_item_and_none_leaves_nothing_out():
    assert _globs(tags=["review-exclude:docs/**"], exclude="a.txt, b/**")[0] == ["a.txt", "b/**"]
    assert _globs(exclude="none")[0] == []


def test_include_pinned_sends_the_pin_files():
    assert _globs(include_pinned=True)[1] == []
