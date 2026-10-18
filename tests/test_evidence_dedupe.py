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


def test_row_puts_the_blob_id_in_oid_for_both_grammars():
    t = E._row("100644 blob abc\tp q", index=False)
    assert (t.mode, t.oid, t.extra, t.path) == ("100644", "abc", "blob", "p q")
    i = E._row("100644 abc 0\tp", index=True)
    assert (i.mode, i.oid, i.extra, i.path) == ("100644", "abc", "0", "p")
    assert E._row("garbage", index=False) is None and E._row("100644 blob abc", index=False) is None
    assert E._index_entries(["100644 abc 1\tp"]) is None
    assert E._index_entries(["100644 abc 0\tp"]) == {"p": ("100644", "abc")}


def test_hash_ids_batch_the_paths_and_pair_each_with_its_id(monkeypatch):
    calls: list[int] = []

    class R:
        ok = True

        def __init__(self, paths):
            self.out = "\n".join(f"id-{p}" for p in paths)

    def git(cwd, *args):
        paths = args[2:]  # after "hash-object", "--"
        calls.append(len(paths))
        return R(paths)

    monkeypatch.setattr(E.W, "git", git)
    paths = [f"p{n}" for n in range(E._HASH_BATCH * 2 + 5)]
    ids = E._hash_ids(Path("."), paths)
    assert calls == [E._HASH_BATCH, E._HASH_BATCH, 5]
    assert ids == [f"id-{p}" for p in paths]


def test_hash_ids_refuse_a_short_or_long_reply_that_git_called_ok(monkeypatch):
    class R:
        ok = True

        def __init__(self, out):
            self.out = out

    for reply in ("", "a\nb\nc"):
        monkeypatch.setattr(E.W, "git", lambda cwd, *a, reply=reply: R(reply))
        assert E._hash_ids(Path("."), ["x", "y"]) is None


def test_hash_ids_name_a_dash_w_file_as_a_path(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "-w").write_text("dash")
    ids = E._hash_ids(tmp_path, ["-w"])
    assert ids is not None and len(ids) == 1
    assert E._hash_ids(tmp_path, ["missing-file"]) is None
