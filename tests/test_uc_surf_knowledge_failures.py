"""B6fdead1799: `similar` and `dupes` with an unknown --kind failed on STDOUT (human) or
printed a body of null fields (--json). A failure is a reason on stderr, exit 1, no body."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import run_cli


@pytest.mark.parametrize("argv", [("similar", "some text"), ("dupes",)])
@pytest.mark.parametrize("json_mode", [False, True])
def test_an_unknown_kind_fails_on_stderr_with_no_body(repo: Path, argv, json_mode) -> None:
    full = (("--json",) if json_mode else ()) + (*argv, "--kind", "nosuchkind")
    code, out, err = run_cli(repo, *full)
    assert code == 1
    assert out == "", out
    assert "unknown kind nosuchkind" in err


def test_a_refused_link_under_json_carries_its_refusal(repo: Path) -> None:
    """The link bug: exit 3 with --json printed nothing on stdout."""
    import json

    code, out, _err = run_cli(repo, "--json", "link", "L-nope", "--related", "L-also-nope")
    assert code == 3
    body = json.loads(out)
    assert body["refusal"]["outcome"] == "refused"
    assert "L-nope" in body["refusal"]["reason"]
