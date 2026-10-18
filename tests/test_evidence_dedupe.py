"""The one head/dirt split, the one row parser and the one batched hash-object (DX-evidence-dedupe)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.services.gates import evidence as E


def test_split_identity_is_the_one_head_dirt_split():
    assert E.split_identity("abc+clean") == ("abc", "+", "clean")
    assert E.split_identity("abc") == ("abc", "", "")
    assert E.head_of("abc+st:1") == "abc" and E.head_of("abc") == ""
    assert E.recorded_content("abc+st:1") == "st:1" and E.recorded_content("abc+x") == ""
    assert E.normal_fingerprint(f"abc+{E.LEGACY_CLEAN}") == "abc+clean"


def test_row_reads_ls_tree_and_ls_files_rows_alike():
    assert E._row("100644 blob abc\tp q") == (("100644", "blob", "abc"), "p q")
    assert E._row("100644 abc 0\tp") == (("100644", "abc", "0"), "p")
    assert E._row("garbage") is None and E._row("100644 blob abc") is None
    assert E._index_entries(["100644 abc 1\tp"]) is None
    assert E._index_entries(["100644 abc 0\tp"]) == {"p": ("100644", "abc")}


def test_hash_ids_pair_every_path_across_batches_and_refuse_a_short_reply(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    names = [f"f{n}.txt" for n in range(E._HASH_BATCH + 5)]
    for n in names:
        (tmp_path / n).write_text(n)
    (tmp_path / "-w").write_text("dash")
    ids = E._hash_ids(tmp_path, [*names, "-w"])
    assert ids is not None and len(ids) == len(names) + 1
    one = subprocess.run(
        ["git", "hash-object", "--", names[-1]], cwd=tmp_path, capture_output=True, text=True
    ).stdout.strip()
    assert ids[len(names) - 1] == one
    assert E._hash_ids(tmp_path, ["missing-file"]) is None
