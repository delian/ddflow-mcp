"""`+ [ ]` and `1. [ ]` are task-list items too, and import like `- [ ]`.

They are valid GFM task list items. The importer used to skip them without a word, so a
TODO.md written that way imported nothing and said nothing about it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli


def _titles(repo: Path, text: str) -> list[str]:
    (repo / "TODO.md").write_text(text)
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "--json", "import")
    assert code in (0, 2), out + err
    return [f["title"] for f in json.loads(out)["found"] if f["kind"] == "task"]


def test_plus_and_numbered_checkboxes_import_as_tasks(repo):
    titles = _titles(
        repo,
        "# T\n\n## Work\n\n- [ ] dash item\n* [ ] star item\n+ [ ] plus item\n"
        "1. [ ] numbered item\n12) [ ] paren item\n",
    )
    assert [t for t in titles if "item" in t] == [
        "dash item",
        "star item",
        "plus item",
        "numbered item",
        "paren item",
    ], titles


def test_ticked_plus_and_numbered_forms_are_done_like_dash(repo):
    (repo / "TODO.md").write_text("# T\n\n## Work\n\n+ [x] a done\n1. [X] b done\n- [ ] c open\n")
    run_cli(repo, "init")
    _code, out, _ = run_cli(repo, "--json", "import")
    found = {f["title"]: f for f in json.loads(out)["found"] if f["kind"] == "task"}
    assert "c open" in found
    assert "a done" not in found and "b done" not in found  # finished: left out as with '-'


def test_a_plain_numbered_item_without_a_box_is_not_a_task(repo):
    titles = _titles(repo, "# T\n\n## Work\n\n1. not a task\n+ also not\n- [ ] real\n")
    assert titles == ["real"], titles
