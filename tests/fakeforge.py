"""A stand-in for the `gh` CLI, backed by a REAL bare git remote.

Real where it matters: pushes land in an actual bare repository, a merge is an actual
`git merge` pushed to it, and head/merge shas are read from it -- so a test that checks
"the merge commit is on main" is checking git, not this file. What is faked is only the
forge's bookkeeping (numbers, reviews, checks), kept in one JSON file the test can edit
to play the reviewer.

Installed by `install(tmp_path, remote)`, which writes an executable `gh` onto a bin dir
the test prepends to PATH. Every invocation is appended to `calls` in the state, so a
test can assert what was (and was NOT) asked of the forge.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

STATE_ENV = "FAKEFORGE_STATE"


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def _save(path: Path, st: dict) -> None:
    path.write_text(json.dumps(st, indent=2))


def _git(*args: str, cwd: str | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _sha(remote: str, branch: str) -> str:
    try:
        return _git("--git-dir", remote, "rev-parse", f"refs/heads/{branch}")
    except subprocess.CalledProcessError:
        return ""


def _view(st: dict, pr: dict) -> dict:
    head_sha = pr.get("merged_head") or _sha(st["remote"], pr["head"])
    return {
        "number": pr["number"],
        "url": pr["url"],
        "state": pr["state"],
        "isDraft": pr.get("draft", False),
        "reviewDecision": pr.get("review", "REVIEW_REQUIRED"),
        "statusCheckRollup": pr.get("checks", []),
        "baseRefName": pr["base"],
        "headRefName": pr["head"],
        "headRefOid": head_sha,
        "mergeCommit": {"oid": pr["merge_sha"]} if pr.get("merge_sha") else None,
        "latestReviews": pr.get("reviews", []),
    }


def _arg(argv: list[str], flag: str, default: str = "") -> str:
    return argv[argv.index(flag) + 1] if flag in argv else default


def merge_on_remote(st: dict, pr: dict, how: str = "--merge") -> str:
    """Perform the merge for real, in a scratch clone, and push it to the remote."""
    remote = st["remote"]
    with tempfile.TemporaryDirectory() as tmp:
        _git("clone", "-q", remote, tmp)
        for k, v in (("user.email", "forge@example.com"), ("user.name", "Forge")):
            _git("config", k, v, cwd=tmp)
        _git("checkout", "-q", pr["base"], cwd=tmp)
        if how == "--squash":
            _git("merge", "--squash", f"origin/{pr['head']}", cwd=tmp)
            _git("commit", "-qm", f"{pr['title']} (#{pr['number']})", cwd=tmp)
        else:
            _git(
                "merge", "--no-ff", "-m", f"Merge #{pr['number']}", f"origin/{pr['head']}", cwd=tmp
            )
        _git("push", "-q", "origin", pr["base"], cwd=tmp)
        sha = _git("rev-parse", "HEAD", cwd=tmp)
    pr["merged_head"] = _sha(remote, pr["head"])
    pr["state"] = "MERGED"
    pr["merge_sha"] = sha
    return sha


def main(argv: list[str]) -> int:  # noqa: C901 -- one branch per faked verb
    path = Path(os.environ[STATE_ENV])
    st = _load(path)
    st.setdefault("calls", []).append(argv)
    prs = st.setdefault("prs", [])

    def done(code: int = 0, out: str = "", err: str = "") -> int:
        _save(path, st)
        if out:
            print(out)
        if err:
            print(err, file=sys.stderr)
        return code

    if st.get("offline"):
        return done(1, err="error connecting to api.github.com: could not resolve host")
    if argv[:2] == ["pr", "list"]:
        head = _arg(argv, "--head")
        rows = [{"number": p["number"], "state": p["state"]} for p in prs if p["head"] == head]
        return done(out=json.dumps(rows))
    if argv[:2] == ["pr", "view"]:
        pr = next((p for p in prs if p["number"] == int(argv[2])), None)
        if pr is None:
            return done(1, err=f"no pull requests found for {argv[2]}")
        return done(out=json.dumps(_view(st, pr)))
    if argv[:2] == ["pr", "create"]:
        head, base = _arg(argv, "--head"), _arg(argv, "--base")
        if not _sha(st["remote"], head):
            return done(1, err=f"head branch {head} does not exist on the remote")
        number = len(prs) + 1
        pr = {
            "number": number,
            "url": f"https://github.com/o/r/pull/{number}",
            "state": "OPEN",
            "head": head,
            "base": base,
            "title": _arg(argv, "--title"),
            "body": Path(_arg(argv, "--body-file")).read_text(),
            "draft": "--draft" in argv,
            "review": "REVIEW_REQUIRED",
        }
        prs.append(pr)
        return done(out=pr["url"])
    if argv[:2] == ["pr", "merge"]:
        pr = next(p for p in prs if p["number"] == int(argv[2]))
        if "--auto" in argv:
            pr["auto"] = True
            return done()
        if st.get("protected") and pr.get("review") != "APPROVED":
            return done(1, err="the base branch policy prohibits the merge")
        want = _arg(argv, "--match-head-commit")
        if want and want != _sha(st["remote"], pr["head"]):
            return done(1, err="head branch was modified")
        how = next((f for f in ("--merge", "--squash", "--rebase") if f in argv), "--merge")
        merge_on_remote(st, pr, how)
        return done()
    if argv[:2] == ["pr", "edit"]:
        pr = next(p for p in prs if p["number"] == int(argv[2]))
        pr["base"] = _arg(argv, "--base", pr["base"])
        return done()
    if argv[0] == "api" and argv[1].endswith("/comments"):
        number = int(argv[1].split("/")[-2])
        pr = next(p for p in prs if p["number"] == number)
        return done(out=json.dumps(pr.get("comments", [])))
    return done(2, err=f"fake gh: unsupported {argv}")


def install(tmp: Path, remote: Path) -> tuple[Path, Path]:
    """Write the fake `gh` and its state file. Returns (bin_dir, state_file)."""
    bindir = tmp / "fakebin"
    bindir.mkdir(exist_ok=True)
    state = tmp / "forge.json"
    _save(state, {"remote": str(remote), "prs": [], "calls": []})
    exe = bindir / "gh"
    exe.write_text(
        f"#!{sys.executable}\n"
        f"import sys; sys.path.insert(0, {str(Path(__file__).parent)!r})\n"
        "import fakeforge; sys.exit(fakeforge.main(sys.argv[1:]))\n"
    )
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return bindir, state


class Forge:
    """The test's handle on the fake: read it, and play the reviewer."""

    def __init__(self, state: Path):
        self.path = state

    @property
    def st(self) -> dict:
        return _load(self.path)

    def pr(self, number: int) -> dict:
        return next(p for p in self.st["prs"] if p["number"] == number)

    def edit(self, number: int, **fields) -> None:
        st = self.st
        pr = next(p for p in st["prs"] if p["number"] == number)
        pr.update(fields)
        _save(self.path, st)

    def set(self, **fields) -> None:
        st = self.st
        st.update(fields)
        _save(self.path, st)

    def _head(self, number: int) -> str:
        return _sha(self.st["remote"], self.pr(number)["head"])

    def approve(self, number: int, *, decision: bool = True) -> None:
        """Approve the CURRENT head. ``decision=False`` plays a repo with no required-
        review rule, where GitHub reports `reviewDecision` as null."""
        self.edit(
            number,
            review="APPROVED" if decision else None,
            reviews=[
                {
                    "author": {"login": "alice"},
                    "state": "APPROVED",
                    "body": "",
                    "commit": {"oid": self._head(number)},
                }
            ],
        )

    def request_changes(
        self, number: int, body: str, comments: list[dict] | None = None, *, decision: bool = True
    ) -> None:
        self.edit(
            number,
            review="CHANGES_REQUESTED" if decision else None,
            reviews=[
                {
                    "author": {"login": "alice"},
                    "state": "CHANGES_REQUESTED",
                    "body": body,
                    "commit": {"oid": self._head(number)},
                }
            ],
            comments=comments or [],
        )

    def merge_as_human(self, number: int, how: str = "--merge") -> str:
        st = self.st
        pr = next(p for p in st["prs"] if p["number"] == number)
        sha = merge_on_remote(st, pr, how)
        _save(self.path, st)
        return sha

    def calls(self, *prefix: str) -> list[list[str]]:
        return [c for c in self.st["calls"] if c[: len(prefix)] == list(prefix)]
