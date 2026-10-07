"""Config writers refuse a `[[reviewer]]` block the reviewer loader would not accept
(bug B96fd182086).

`append_block` checked TOML syntax only, and `_append_config`'s `Config.check` passes over
`[[reviewer]]` (another module's table). A misspelt field was written: in the git-ignored
local layer (the default target) it is skipped with a warning on every later read, so the
setting the operator meant never takes effect; in a committed file of ddflow's own tree it
makes every review fail to load its reviewers.
"""

from __future__ import annotations

import pytest

from ddflow.services.configwrite import _append_config, append_block

TYPO = '[[reviewer]]\nname = "x"\nmodel = "m"\nbase_url = "http://127.0.0.1:1/v1"\nmodle = "typo"\n'
GOOD = '[[reviewer]]\nname = "x"\nmodel = "m"\nbase_url = "http://127.0.0.1:1/v1"\n'


def _config_files(repo) -> dict:
    """Every config file under .ddflow, by content. The local layer's own .gitignore and
    the write locks are not config, on either side of the comparison."""
    root = repo / ".ddflow"
    return {
        p: p.read_bytes()
        for p in (root.rglob("*") if root.exists() else ())
        if p.is_file() and not p.name.endswith((".lock", ".gitignore"))
    }


@pytest.mark.parametrize("shared", [False, True])
def test_a_reviewer_block_with_an_unknown_field_is_refused_and_nothing_is_written(repo, shared):
    before = _config_files(repo)

    with pytest.raises(ValueError, match="modle"):
        append_block(repo, TYPO, shared=shared, own="reviewers.toml")

    assert _config_files(repo) == before, "a refused block still changed a config file"


@pytest.mark.parametrize("local", [False, True])
def test_config_append_toml_refuses_it_too(repo, local):
    """`ddflow config --append-toml` / `ddflow_configure`: `Config.check` passes over
    `[[reviewer]]`, so it needed the same check."""
    before = _config_files(repo)

    error, _path = _append_config(repo, TYPO, local=local)

    assert "modle" in str(error), error
    assert _config_files(repo) == before, "a refused block still changed a config file"


def test_a_valid_reviewer_block_is_still_written(repo):
    from ddflow.services.review import load_reviewers

    path = append_block(repo, GOOD, own="reviewers.toml")
    assert path.is_file()
    assert [r.name for r in load_reviewers(repo)] == ["x"]


@pytest.mark.parametrize(
    "block", ['[reviewer]\nname = "x"\nmodle = "typo"\n', 'reviewer = "x"\n', "reviewer = [1]\n"]
)
def test_a_reviewer_that_is_not_an_array_of_tables_is_refused(repo, block):
    """Read back, any of these is no reviewer at all, with no error."""
    before = _config_files(repo)
    with pytest.raises(ValueError, match=r"\[\[reviewer\]\]"):
        append_block(repo, block, own="reviewers.toml")
    error, _path = _append_config(repo, block, local=True)
    assert "[[reviewer]]" in str(error), error
    assert _config_files(repo) == before
