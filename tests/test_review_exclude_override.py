"""Per-run and per-item overrides of `[review].exclude_globs` (DX-review-exclude-per-item)."""

from types import SimpleNamespace

from ddflow.services import review_exclude as REX

CFG = SimpleNamespace(review=SimpleNamespace(exclude_globs=["*.lock"], exclude_pin_files=True))


def _globs(monkeypatch, tags=None, exclude="", include_pinned=False):
    monkeypatch.setattr(REX.pins, "pin_files", lambda repo, cfg: ["golden.ambr"])
    return REX.patterns(CFG, None, tags, exclude, include_pinned)


def test_config_is_the_default(monkeypatch):
    assert _globs(monkeypatch) == (["*.lock"], ["golden.ambr"])


def test_item_tags_replace_the_config_globs(monkeypatch):
    assert _globs(monkeypatch, tags=["x", "review-exclude:docs/**"])[0] == ["docs/**"]


def test_the_run_flag_beats_the_item_and_none_leaves_nothing_out(monkeypatch):
    assert _globs(monkeypatch, tags=["review-exclude:docs/**"], exclude="a.txt, b/**")[0] == [
        "a.txt",
        "b/**",
    ]
    assert _globs(monkeypatch, exclude="none")[0] == []


def test_include_pinned_sends_the_pin_files(monkeypatch):
    assert _globs(monkeypatch, include_pinned=True)[1] == []


def test_a_run_override_reaches_the_reviewer(repo, tmp_path):
    from test_review_exclude import _setup

    import ddflow.api.review as api

    seen = _setup(repo, tmp_path)
    api.review(repo, gate="critic", item="T1", exclude="none")
    assert "GENERATED_MARK" in seen.read_text()  # the default would have left it out
    seen.unlink()
    api.review(repo, gate="critic", item="T1", exclude="x.py", force=True, reason="again")
    sent = seen.read_text()
    assert "x = 1" not in sent and "GENERATED_MARK" in sent  # replaces the config globs


def test_an_item_tag_and_include_pinned_reach_the_reviewer(repo, tmp_path):
    from conftest import run_cli
    from test_review_exclude import _setup

    import ddflow.api.review as api

    seen = _setup(repo, tmp_path, "[review]\nexclude_globs = []\n")
    run_cli(repo, "update", "T1", "--add-tags", "review-exclude:x.py")
    api.review(repo, gate="critic", item="T1")
    assert "x = 1" not in seen.read_text()


def test_include_pinned_reaches_the_reviewer(repo, tmp_path):
    from test_review_exclude import _setup

    import ddflow.api.review as api

    seen = _setup(repo, tmp_path, "[review]\nexclude_globs = []\n")
    (repo / ".ddflow" / "pins.toml").write_text(
        '[[pin]]\nname = "p"\nfiles = ["tests/golden/snap.json"]\ncheck = ["true"]\nregen = ["true"]\n'
    )
    api.review(repo, gate="critic", item="T1")
    assert "GENERATED_MARK" not in seen.read_text()
    seen.unlink()
    api.review(repo, gate="critic", item="T1", include_pinned=True, force=True, reason="again")
    assert "GENERATED_MARK" in seen.read_text()
