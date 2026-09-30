"""The item trailer must NAME an item in the queue (bug B438d336b24).

`[enforce] require_item_trailer` accepted any non-empty value, so in run_nemo_run a
commit ending `Phase: NOPE.99` passed the commit-msg hook with exit 0: a mistyped id
reached history, and the audit the trailer exists for -- matching shipped commits to
queue items with `git log --format='%(trailers:key=Phase)'` -- could not match it.

Now a trailer whose key is in `item_trailer_keys` must carry the id of an item in the
queue (a phase or a task, in any state but removed). A key that marks a commit shipping
NO item (`Phase-ships`) is declared in `[enforce] trailer_waivers` with the only words it
may carry. A queue that cannot be read is exit 2, never a pass.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import Config

WORDS = ["none", "filing", "recon", "evidence", "followup"]

#: run_nemo_run's arrangement: `Phase: <id>`, or `Phase-ships: <word>` for a commit that
#: ships no item.
NEMO = (
    "[enforce]\n"
    "require_item_trailer = true\n"
    'item_trailer_keys = ["Phase", "Phase-ships"]\n'
    f'trailer_waivers = {{ "Phase-ships" = {json.dumps(WORDS)} }}\n'
)


def _queue(repo: Path, config: str = NEMO) -> None:
    """A queue with a phase `160.D`, its task `160.D.4`, and a removed task `160.D.9`."""
    assert run_cli(repo, "init")[0] == 0
    (repo / ".ddflow" / "config.toml").write_text(config)
    assert run_cli(repo, "phase", "add", "160.D", "--title", "phase")[0] == 0
    for t in ("160.D.4", "160.D.9"):
        code, out, err = run_cli(repo, "task", "add", t, "--phase", "160.D", "--title", t)
        assert code == 0, out + err
    code, out, err = run_cli(repo, "remove", "160.D.9", "--reason", "dropped")
    assert code == 0, out + err


def _check(repo: Path, message: str, *, cwd: Path | None = None) -> tuple[int, str]:
    """`hooks check-msg`, run the way git runs it: from inside the checkout."""
    f = repo.parent / "COMMIT_MSG"
    f.write_text(message)
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    p = subprocess.run(
        [sys.executable, "-m", "ddflow", "hooks", "check-msg", str(f)],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd or repo,
        timeout=300,
    )
    return p.returncode, p.stdout + p.stderr


# -- the regression ----------------------------------------------------------------------


def test_a_trailer_naming_no_item_is_refused_and_the_refusal_names_it(repo):
    _queue(repo)
    code, out = _check(repo, "subject\n\nPhase: NOPE.99\n")
    assert code == 1, f"'Phase: NOPE.99' names no item and passed (exit {code})\n{out}"
    assert "Phase" in out and "NOPE.99" in out, out
    assert "git commit --amend" in out, "the refusal must say how to fix the message"


def test_a_real_task_id_passes(repo):
    _queue(repo)
    code, out = _check(repo, "subject\n\nPhase: 160.D.4\n")
    assert code == 0, out


def test_a_phase_id_passes(repo):
    _queue(repo)
    code, out = _check(repo, "subject\n\nPhase: 160.D\n")
    assert code == 0, out


def test_a_removed_items_id_is_refused(repo):
    _queue(repo)
    code, out = _check(repo, "subject\n\nPhase: 160.D.9\n")
    assert code == 1, f"a removed item's id passed\n{out}"
    assert "160.D.9" in out


def test_a_waiver_key_takes_its_declared_word(repo):
    _queue(repo)
    code, out = _check(repo, "docs only\n\nPhase-ships: none\n")
    assert code == 0, out


def test_a_waiver_key_refuses_any_other_word_and_lists_the_allowed_ones(repo):
    _queue(repo)
    code, out = _check(repo, "docs only\n\nPhase-ships: bogus\n")
    assert code == 1, f"'Phase-ships: bogus' passed\n{out}"
    assert "Phase-ships" in out and "bogus" in out, out
    for w in WORDS:
        assert w in out, f"the allowed word {w!r} is not listed\n{out}"


def test_one_invalid_trailer_is_refused_even_beside_a_valid_one(repo):
    _queue(repo)
    code, out = _check(repo, "subject\n\nPhase: 160.D.4\nPhase: NOPE.99\n")
    assert code == 1, f"an invalid trailer rode along with a valid one\n{out}"
    assert "NOPE.99" in out
    code, out = _check(repo, "subject\n\nPhase: 160.D.4\nPhase-ships: bogus\n")
    assert code == 1, out


def test_a_queue_that_cannot_be_read_is_could_not_run_never_a_pass(repo):
    _queue(repo)
    shutil.rmtree(repo / ".ddflow" / "events")
    code, out = _check(repo, "subject\n\nPhase: 160.D.4\n")
    assert code == 2, f"an unreadable queue must be exit 2 (could not run), got {code}\n{out}"
    assert "could not" in out.lower(), out  # the exit code is the contract


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root reads anything")
def test_an_unreadable_shard_is_could_not_run(repo):
    _queue(repo)
    shards = list((repo / ".ddflow" / "events").glob("*.jsonl"))
    for s in shards:
        s.chmod(0)
    try:
        code, out = _check(repo, "subject\n\nPhase: 160.D.4\n")
    finally:
        for s in shards:
            s.chmod(0o644)
    assert code == 2, f"exit {code}\n{out}"


def test_a_merge_commit_is_exempt(repo):
    _queue(repo)
    code, out, err = run_cli(repo, "hooks", "install")
    assert code == 0, out + err
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)

    def commit(name: str, msg: str) -> subprocess.CompletedProcess:
        (repo / name).write_text(name)
        subprocess.run(["git", "-C", str(repo), "add", name], check=True)
        return subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", msg], capture_output=True, text=True
        )

    assert commit("base.txt", "base\n\nPhase: 160.D").returncode == 0
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "feat"], check=True)
    assert commit("f.txt", "feature\n\nPhase: 160.D.4").returncode == 0
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)
    assert commit("m.txt", "main moves\n\nPhase-ships: none").returncode == 0
    refused = commit("n.txt", "typo\n\nPhase: NOPE.99")
    assert refused.returncode != 0 and "NOPE.99" in refused.stderr, refused.stderr
    subprocess.run(["git", "-C", str(repo), "rm", "-q", "--cached", "n.txt"], check=True)
    (repo / "n.txt").unlink()
    # The merge message carries no trailer at all: a merge is exempt.
    merge = subprocess.run(
        ["git", "-C", str(repo), "merge", "--no-ff", "-q", "-m", "Merge feat", "feat"],
        capture_output=True,
        text=True,
    )
    assert merge.returncode == 0, merge.stderr


# -- around it ---------------------------------------------------------------------------


def test_a_near_miss_is_offered(repo):
    _queue(repo)
    code, out = _check(repo, "subject\n\nPhase: 160.d.4\n")
    assert code == 1
    assert "160.D.4" in out, f"a case-only typo should suggest the real id\n{out}"


def test_the_check_is_off_when_the_trailer_is_not_required(repo):
    _queue(repo, NEMO.replace("require_item_trailer = true", "require_item_trailer = false"))
    assert _check(repo, "subject\n\nPhase: NOPE.99\n")[0] == 0
    assert _check(repo, "subject, no trailer at all\n")[0] == 0


def test_a_waiver_key_is_not_an_escape_from_the_id_check_for_other_keys(repo):
    """`Phase: none` is not a waiver: only the waiver KEY takes words."""
    _queue(repo)
    assert _check(repo, "subject\n\nPhase: none\n")[0] == 1


def test_without_waivers_every_accepted_key_must_name_an_item(repo):
    _queue(repo, NEMO.replace("trailer_waivers", "#trailer_waivers"))
    code, out = _check(repo, "docs\n\nPhase-ships: none\n")
    assert code == 1, out
    assert "trailer_waivers" in out, "the refusal should name the knob that would allow it"


def test_the_fresh_index_and_the_fold_agree(repo):
    """A fresh `.ddflow/index.db` answers the id lookup; one the log has moved past is
    not trusted. Both must honour removal."""
    _queue(repo)
    assert run_cli(repo, "rebuild")[0] == 0
    assert _check(repo, "s\n\nPhase: 160.D.4\n")[0] == 0
    assert _check(repo, "s\n\nPhase: 160.D.9\n")[0] == 1
    # The log moves on; the index is now stale and must not hide the new item.
    assert run_cli(repo, "task", "add", "160.D.5", "--phase", "160.D", "--title", "x")[0] == 0
    assert _check(repo, "s\n\nPhase: 160.D.5\n")[0] == 0
    assert run_cli(repo, "remove", "160.D.4", "--reason", "gone")[0] == 0
    assert _check(repo, "s\n\nPhase: 160.D.4\n")[0] == 1


def test_a_commit_in_a_linked_worktree_reads_the_primary_checkouts_queue(repo):
    """The hook runs inside the worktree during `git commit`; the queue it checks against
    is the primary's, resolved exactly as the pre-commit lease check resolves it."""
    _queue(repo)
    run_cli(repo, "hooks", "install")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "--no-verify", "-m", "queue"], check=True
    )
    wt = repo.parent / "wt"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-q", "-b", "w", str(wt)], check=True
    )
    # An item added AFTER the worktree forked exists only in the primary's log.
    assert run_cli(repo, "task", "add", "160.D.6", "--phase", "160.D", "--title", "x")[0] == 0
    (wt / "x.txt").write_text("x")
    subprocess.run(["git", "-C", str(wt), "add", "x.txt"], check=True)
    ok = subprocess.run(
        ["git", "-C", str(wt), "commit", "-q", "-m", "x\n\nPhase: 160.D.6"],
        capture_output=True,
        text=True,
    )
    assert ok.returncode == 0, ok.stderr
    (wt / "y.txt").write_text("y")
    subprocess.run(["git", "-C", str(wt), "add", "y.txt"], check=True)
    bad = subprocess.run(
        ["git", "-C", str(wt), "commit", "-q", "-m", "y\n\nPhase: NOPE.99"],
        capture_output=True,
        text=True,
    )
    assert bad.returncode != 0 and "NOPE.99" in bad.stderr, bad.stderr


def test_the_waiver_map_is_validated_on_load():
    Config.check({"enforce": {"trailer_waivers": {"Phase-ships": WORDS}}})
    for bad in (
        {"Phase-ships": "none"},
        {"Phase-ships": []},
        {"Phase-ships": ["none", ""]},
        {"Phase-ships ": ["none"]},
        {"Phase ships": ["none"]},
        {"": ["none"]},
        {"Phase-ships:": ["none"]},
        ["Phase-ships"],
    ):
        with pytest.raises(ValueError, match="trailer_waivers"):
            Config.check({"enforce": {"trailer_waivers": bad}})


def test_the_waiver_map_from_the_environment(tmp_path):
    env = {"DDFLOW_ENFORCE_TRAILER_WAIVERS": json.dumps({"Phase-ships": ["none", "recon"]})}
    cfg = Config.load(tmp_path, env=env)
    assert cfg.enforce.trailer_waivers == {"Phase-ships": ["none", "recon"]}
    assert Config().enforce.trailer_waivers == {}
    # A string where the type says a list is refused by the coercion itself (critic).
    from ddflow.config import _coerce_dict

    with pytest.raises(ValueError, match="object of lists"):
        _coerce_dict('{"Phase-ships": "none"}', "dict[str, list[str]]")
    with pytest.raises(ValueError, match="object of lists"):
        _coerce_dict("Phase-ships=none", "dict[str, list[str]]")
    assert _coerce_dict('{"a": "b"}', "dict[str, str]") == {"a": "b"}
    # Never str()-cast into a word: `null` would become "None" (critic, round 3).
    for bad in ('{"Phase-ships": [null]}', '{"Phase-ships": [1]}', '{"Phase-ships": [["none"]]}'):
        with pytest.raises(ValueError, match="object of lists"):
            _coerce_dict(bad, "dict[str, list[str]]")
    # An empty value is an empty map, as it is an empty list for a list knob.
    assert (
        Config.load(tmp_path, env={"DDFLOW_ENFORCE_TRAILER_WAIVERS": ""}).enforce.trailer_waivers
        == {}
    )


# -- roborev on the branch ---------------------------------------------------------------


def test_a_waiver_declared_alone_is_accepted_not_inert(repo):
    """A waiver key need not be repeated in item_trailer_keys: the waiver map declares
    it (roborev, then both cross-family reviewers over three rounds)."""
    _queue(repo, NEMO.replace('["Phase", "Phase-ships"]', '["Phase"]'))
    code, out = _check(repo, "docs\n\nPhase-ships: none\n")
    assert code == 0, out
    code, out = _check(repo, "docs\n\nPhase-ships: bogus\n")
    assert code == 1 and "bogus" in out, out
    code, out = _check(repo, "docs, no trailer\n")
    assert code == 1, out
    assert "Phase-ships" in out and all(w in out for w in WORDS), out


def test_git_failing_to_parse_the_trailers_is_could_not_run(monkeypatch):
    from ddflow.services import enforce as E

    def broken(*_a, **_k):
        raise E.P.TimeoutExpired("git", 30)

    monkeypatch.setattr(E.P, "run", broken)
    code, msg = E.check_item_trailer("s\n\nItem: T1\n", ["Item"], ids=lambda: {"T1"})
    assert code == 2, msg
    monkeypatch.setattr(
        E.P, "run", lambda *a, **k: subprocess.CompletedProcess(a, 128, "", "fatal")
    )
    assert E.check_item_trailer("s\n\nItem: T1\n", ["Item"], ids=lambda: {"T1"})[0] == 2


# -- the cross-family reviewers on the branch -------------------------------------------


def test_a_waiver_key_outside_item_trailer_keys_still_takes_only_its_words(repo):
    """rubber-duck (deepseek): with `item_trailer_keys = ["Phase"]`, `Phase-ships: bogus`
    beside a valid `Phase:` passed unexamined."""
    _queue(repo, NEMO.replace('["Phase", "Phase-ships"]', '["Phase"]'))
    code, out = _check(repo, "s\n\nPhase: 160.D.4\nPhase-ships: bogus\n")
    assert code == 1 and "bogus" in out, out
    assert _check(repo, "s\n\nPhase: 160.D.4\nPhase-ships: none\n")[0] == 0


def test_an_id_in_the_body_is_not_a_trailer(repo):
    """critic (deepseek) read `_trailers` as a line scan: it is git's own parser, so a
    real id outside the final paragraph does not satisfy the check."""
    _queue(repo)
    code, out = _check(repo, "Fix the parser\n\nPhase: 160.D.4\n\n(refs: #12)\n")
    assert code == 1 and "no `Phase: <id>`" in out, out


def test_config_set_takes_the_documented_toml_form(repo):
    """roborev (deepseek): the knob's doc must name the form `config --set` accepts."""
    run_cli(repo, "init")
    code, out, err = run_cli(
        repo, "config", "--set", "enforce.trailer_waivers", '{ "Phase-ships" = ["none"] }'
    )
    assert code == 0, out + err
    assert Config.load(repo).enforce.trailer_waivers == {"Phase-ships": ["none"]}
    from ddflow.config import KNOB_DOCS

    assert (
        'config --set enforce.trailer_waivers \'{ "Phase-ships" = ["none"] }\''
        in (KNOB_DOCS["enforce.trailer_waivers"])
    )


def test_a_dict_knob_is_recognised_by_any_spelling_of_its_type():
    """rubber-duck (deepseek): `startswith("dict")` missed `dict` itself and a wrapped
    `Optional[dict[...]]`, which the substring test it replaced had handled."""
    from ddflow.config import _coerce

    assert _coerce("a=b", dict) == {"a": "b"}
    assert _coerce('{"a": "b"}', "Optional[dict[str, str]]") == {"a": "b"}
    assert _coerce("a,b", "list[str]") == ["a", "b"]
    # ...and a list OF dicts is still a list (rubber-duck, round 5).
    assert _coerce("", "list[dict[str, str]]") == []
    assert isinstance(_coerce('[{"a": "b"}]', "list[dict[str, str]]"), list)
    from ddflow.config import _outer_is_dict

    assert _outer_is_dict("dict[str, list[str]]") and _outer_is_dict("<class 'dict'>")
    assert not _outer_is_dict("list[dict[str, str]]") and not _outer_is_dict("list[str]")


def test_no_trailer_under_a_waiver_only_config_is_a_refusal_not_a_crash():
    """rubber-duck (deepseek): the example line indexed `keys[0]`, an IndexError when a
    caller passes no item keys. The hook itself substitutes ["Item"] for an empty list."""
    from ddflow.services import enforce as E

    code, msg = E.check_item_trailer(
        "subject only\n", [], ids=set, waivers={"Phase-ships": ["none", "recon"]}
    )
    assert code == 1 and "Phase-ships: none" in msg, msg
    assert (
        E.check_item_trailer(
            "s\n\nPhase-ships: recon\n", [], ids=set, waivers={"Phase-ships": ["none", "recon"]}
        )[0]
        == 0
    )


def test_git_commit_verbose_keeps_the_trailer_readable(repo, tmp_path):
    """critic (deepseek): with `git commit -v` the message file ends in the diff below
    the scissors line. `git interpret-trailers` honours the scissors line, so the trailer
    above it is still read -- a valid id commits, a bad one is refused."""
    _queue(repo)
    run_cli(repo, "hooks", "install")
    editor = tmp_path / "ed.sh"

    def commit_verbose(trailer: str) -> subprocess.CompletedProcess:
        editor.write_text(
            '#!/bin/sh\nf="$1"; { printf \'subject\\n\\n%s\\n\' "' + trailer + '"; cat "$f"; }'
            ' > "$f.new" && mv "$f.new" "$f"\n'
        )
        editor.chmod(0o755)
        (repo / "v.txt").write_text(trailer)
        subprocess.run(["git", "-C", str(repo), "add", "v.txt"], check=True)
        return subprocess.run(
            ["git", "-C", str(repo), "commit", "-v", "-q"],
            capture_output=True,
            text=True,
            env={**os.environ, "GIT_EDITOR": str(editor)},
        )

    ok = commit_verbose("Phase: 160.D.4")
    assert ok.returncode == 0, ok.stderr
    bad = commit_verbose("Phase: NOPE.99")
    assert bad.returncode != 0 and "NOPE.99" in bad.stderr, bad.stderr
