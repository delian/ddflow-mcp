"""The ids `ddflow split` mints for un-named children (FOC-split-id-bug).

Split used to mint child ids with an EMPTY prefix -- `.1-emitter`, `.2-registry`,
`.3-tag`, `.4-mcp-output` -- so a slashed child id duplicated another item's and the
dedupe sweep paired them at 1.00. The `[ids].split_child` template (`{parent}.{seq}`)
fixed the minting: a split that does not name each child now mints `<parent>.<seq>`.

These tests pin that invariant. The strays themselves (`.1-emitter`, `.2-registry`,
`.3-tag`, `.4-mcp-output`) are data already in the log and are the operator's to
abandon once the minting is pinned; nothing here re-files them.
"""

from __future__ import annotations

from conftest import run_cli

from ddflow import api


def _two_things(repo) -> str:
    """A plain task named ``T1`` in a fresh project, to split."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py", "--title", "two things")
    return "T1"


def test_split_auto_mints_each_child_under_the_parent_id(repo):
    """A child the caller did NOT name keeps the parent's id as its prefix.

    An empty ``sub-id`` on a `--into` spec ("=thing one") is the "no name" case: the id
    is minted, so it must read `<parent>.<seq>`, never an id that starts with `.` or `-`
    (which is not a valid branch name and not the parent's).
    """
    _two_things(repo)

    out = api.split(repo, "T1", into=["=thing one", "=thing two"])
    assert out.exit == 0, out
    created = out.data["created"]
    assert created == ["T1.1", "T1.2"], out.data
    for child in created:
        assert child.startswith("T1."), child
        assert not child.startswith(".") and not child.startswith("-"), child


def test_split_never_mints_an_empty_prefix_child_id(repo):
    """The empty-prefix duplication this task exists for, probed directly.

    The dedupe sweep paired `.1-emitter` with `B-uni-compat-json.2-emitter`: the same
    child text under two ids, one with an empty parent prefix. A minted child must carry
    the parent's id; if minting ever drops it again the created ids start with a dot and
    this test is the one that catches it.
    """
    parent = _two_things(repo)

    out = api.split(repo, parent, into=["=first thing", "=second thing"])
    assert out.exit == 0, out
    for created in out.data["created"]:
        assert created.startswith(f"{parent}."), created
        assert not created.startswith(".") and not created.startswith("-"), created
