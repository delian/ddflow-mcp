"""`append_block` refuses a `[[reviewer]]` block the reviewer loader would not accept
(bug B96fd182086).

It checked TOML syntax only, while `_write_config` and `_append_config` run the schema
checks. A misspelt field was written: in the git-ignored local layer (the default target)
it is skipped with a warning on every later read, so the setting the operator meant never
takes effect; in a committed file of ddflow's own tree it makes every review fail to load
its reviewers.
"""

from __future__ import annotations

import pytest

from ddflow.services.configwrite import append_block

TYPO = '[[reviewer]]\nname = "x"\nmodel = "m"\nbase_url = "http://127.0.0.1:1/v1"\nmodle = "typo"\n'
GOOD = '[[reviewer]]\nname = "x"\nmodel = "m"\nbase_url = "http://127.0.0.1:1/v1"\n'


@pytest.mark.parametrize("shared", [False, True])
def test_a_reviewer_block_with_an_unknown_field_is_refused_and_nothing_is_written(repo, shared):
    before = (
        {p: p.read_bytes() for p in (repo / ".ddflow").rglob("*") if p.is_file()}
        if (repo / ".ddflow").exists()
        else {}
    )

    with pytest.raises(ValueError, match="modle"):
        append_block(repo, TYPO, shared=shared, own="reviewers.toml")

    after = {p: p.read_bytes() for p in (repo / ".ddflow").rglob("*") if p.is_file()}
    # The local layer's own .gitignore and the write lock are not config.
    after = {p: b for p, b in after.items() if not p.name.endswith((".lock", ".gitignore"))}
    assert after == before, "a refused block still changed a config file"


def test_a_valid_reviewer_block_is_still_written(repo):
    from ddflow.services.review import load_reviewers

    path = append_block(repo, GOOD, own="reviewers.toml")
    assert path.is_file()
    assert [r.name for r in load_reviewers(repo)] == ["x"]
