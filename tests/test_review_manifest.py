"""Every review chunk carries a manifest of the whole change (DX-review-manifest).

A chunk was judged alone: 'no templates, no README rows' and 'nothing adds the guard' were
raised about facts a sibling chunk answered.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_review_chunks import _command_reviewer, _new_file

from ddflow.services import review_manifest as RM
from ddflow.services.review import review

DEL = (
    "diff --git a/shard.py b/shard.py\ndeleted file mode 100644\n--- a/shard.py\n+++ /dev/null\n"
    "@@ -1,2 +0,0 @@\n-a\n-b\n"
)
MOD = (
    "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n@@ -1 +1,2 @@\n-old\n+def guard():\n+    pass\n"
)


def test_manifest_lists_every_file_counts_new_and_excluded():
    text = RM.build(_new_file("t/new.md", 3) + DEL + MOD, ["uv.lock"], 40)
    assert "- t/new.md (+3 -0): new file" in text
    assert "- shard.py (+0 -2): deleted" in text
    assert "- m.py (+2 -1): modified; adds guard" in text
    assert "New files: t/new.md" in text
    assert "Left out of review" in text and "uv.lock" in text


def test_manifest_cap_counts_the_rest_and_zero_turns_it_off():
    diff = "".join(_new_file(f"f{n}.txt", 1) for n in range(5))
    assert "and 3 more file(s)" in RM.build(diff, None, 2)
    assert RM.build(diff, None, 0) == ""
    assert RM.build("", None, 5) == ""


def test_every_chunk_prompt_names_the_files_of_its_siblings(tmp_path):
    rev, seen = _command_reviewer(
        tmp_path, "echo 'STATUS: NO FINDINGS'", max_chunk_chars=300
    )
    diff = _new_file("a_guard.py", 4) + DEL + MOD
    res = review(rev, diff, "i", manifest=RM.build(diff, None, 40))
    assert res.chunks_total >= 2
    prompts = seen.read_text().split("\n=====\n")[:-1]
    assert len(prompts) == res.chunks_total
    for p in prompts:
        assert "a_guard.py (+4 -0)" in p and "shard.py (+0 -2)" in p and "m.py (+2 -1)" in p
        assert "This part holds:" in p


def test_no_manifest_leaves_the_prompt_without_the_section(tmp_path):
    rev, seen = _command_reviewer(tmp_path, "echo 'STATUS: NO FINDINGS'")
    review(rev, MOD, "i")
    assert "manifest" not in seen.read_text().lower()
