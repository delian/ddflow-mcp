"""Scenario 2 — an agent is killed mid-task, and the work is recovered, not lost.

This is the scenario the whole lease design exists for. The invented project is a
feed parser; agent DELTA claims a task, commits one change, leaves a second change
uncommitted, and then dies without releasing anything.

What must happen next, in order:

1. Nothing is lost or touched. The worktree still holds both the commit and the
   uncommitted file.
2. While the lease is live, the item stays claimed — a dead agent is indistinguishable
   from a slow one until the lease expires, and guessing is how work gets destroyed.
3. Once expired, `orchard recover` finds it, measures the tree, and says what is in it.
4. A new agent may NOT silently take it. It must acknowledge the situation.
5. After salvage, the item returns to the pool and the work is still on disk.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from harness import Scenario

SCAFFOLD = {
    "README.md": "# feedparse\n",
    ".gitignore": "__pycache__/\n*.pyc\n.pytest_cache/\n",
    "feedparse/__init__.py": "",
    "tests/__init__.py": "",
}


def run(sc: Scenario) -> None:
    sc.head("SCENARIO 2 — crash mid-task, then recovery without losing work")
    sc.make_repo("feedparse", SCAFFOLD)

    sc.step("Set up a short lease TTL so a crash is observable inside a test")
    sc.orchard("init")
    sc.write(
        ".orchard/config.toml",
        """
        [lease]
        ttl_s = 2
        grace_s = 0
        heartbeat_s = 1

        [gates]
        # keep the demo focused on recovery
        required = ["implement", "merge"]
    """,
    )
    sc.orchard("phase", "add", "P1", "--title", "Parsing")
    sc.orchard(
        "task",
        "add",
        "P1.T1",
        "--phase",
        "P1",
        "--title",
        "RSS reader",
        "--globs",
        "feedparse/rss.py",
    )
    sc.orchard(
        "task",
        "add",
        "P1.T2",
        "--phase",
        "P1",
        "--title",
        "Atom reader",
        "--globs",
        "feedparse/atom.py",
    )

    sc.step("Agent DELTA claims the task and starts working")
    sc.orchard("claim", "P1.T1", agent="delta")
    wt = Path(sc.jorchard("show", "P1.T1")["worktree"])
    sc.write(
        "feedparse/rss.py",
        '''
        """RSS 2.0 reader (committed before the crash)."""
        import xml.etree.ElementTree as ET

        def parse(text):
            root = ET.fromstring(text)
            return [{"title": i.findtext("title"), "link": i.findtext("link")}
                    for i in root.iterfind(".//item")]
    ''',
        repo=wt,
    )
    committed = sc.commit_in(wt, "P1.T1: rss reader")
    sc.write(
        "feedparse/rss_dates.py",
        '''
        """Date normalisation — FINISHED but never committed. This file is the whole
        point of the scenario: it exists nowhere except this worktree."""
        from email.utils import parsedate_to_datetime

        def normalise(raw):
            return parsedate_to_datetime(raw).isoformat()
    ''',
        repo=wt,
    )
    sc.check(
        "the worktree holds one commit and one uncommitted file",
        sc.git("rev-list", "--count", "main..HEAD", repo=wt) == "1"
        and (wt / "feedparse/rss_dates.py").exists(),
    )

    sc.step("DELTA is killed — no release, no final heartbeat, nothing")
    sc.note(
        "Simulated exactly as a real kill would leave things: the process simply "
        "stops. No cleanup code runs, because in a real crash none does."
    )

    sc.step("Immediately after the crash, the item is still CLAIMED")
    code, _, err = sc.orchard("claim", "P1.T1", agent="epsilon", expect=3)
    sc.check("another agent is refused while the lease is still live", code == 3)
    sc.check(
        "recover reports nothing yet — a dead agent looks like a slow one",
        sc.orchard("recover", expect=2)[0] == 2,
    )
    sc.note(
        "This is deliberate. Treating a momentarily-quiet agent as dead is how two "
        "agents end up in one worktree."
    )

    sc.step("Wait for the lease to expire, then run recovery")
    time.sleep(2.5)
    found = sc.jorchard("recover")
    sc.check("recovery finds exactly one situation", len(found) == 1, json.dumps(found))
    rec = found[0]
    sc.check("it is identified as an expired lease", rec["kind"] == "expired_lease")
    sc.check("it names the agent that died", rec["holder"] == "delta")
    sc.check(
        "it MEASURED the tree: 1 uncommitted file, 1 unmerged commit",
        rec["dirty_files"] == 1 and rec["unmerged_commits"] == 1,
        json.dumps(rec),
    )
    sc.check("it is flagged as containing salvageable work", rec["salvageable"] is True)
    sc.check(
        "the advice tells the operator to inspect FIRST",
        "INSPECT FIRST" in rec["advice"],
        rec["advice"],
    )

    sc.step("The uncommitted work is STILL THERE — nothing was cleaned up")
    sc.check(
        "the finished-but-uncommitted file survived the crash",
        (wt / "feedparse/rss_dates.py").exists(),
    )
    sc.check(
        "the pre-crash commit survived too",
        sc.git("cat-file", "-e", committed + "^{commit}", repo=wt) == "",
    )

    sc.step("A new agent still cannot silently steal an expired lease")
    code, _, err = sc.orchard("claim", "P1.T1", agent="epsilon", expect=3)
    sc.check("the claim is refused even though the lease expired", code == 3)
    sc.check(
        "the refusal explains WHY it will not auto-reclaim",
        "NOT stolen automatically" in err,
        err[:220],
    )
    sc.note(
        "An automatic reclaim here would be indistinguishable from correct "
        "behaviour right up until the first time it deleted a day's work."
    )

    sc.step("An automatic sweep also leaves salvageable work alone")
    sc.orchard("recover", "--apply")
    st = sc.jorchard("show", "P1.T1")
    sc.check(
        "the claim is still in place after --apply, because work was found",
        st["lease"] is not None,
        json.dumps(st.get("lease")),
    )

    sc.step("The operator salvages, then releases — now the item returns to the pool")
    sc.commit_in(wt, "P1.T1: salvaged date normalisation")
    sc.orchard("release", "P1.T1", "--note", "salvaged 1 file, 2 commits kept")
    sc.orchard("claim", "P1.T1", agent="epsilon", expect=0)
    sc.check(
        "EPSILON adopted the EXISTING worktree rather than making a second one",
        Path(sc.jorchard("show", "P1.T1")["worktree"]) == wt,
    )
    sc.check(
        "and the salvaged work is in that worktree's history",
        "rss_dates" in sc.git("show", "--stat", "HEAD", repo=wt),
    )

    sc.step("Meanwhile an unrelated task was never affected")
    ready = [r["id"] for r in sc.jorchard("next", "--phase", "P1")["ready"]]
    sc.check("T2 stayed available throughout the whole incident", "P1.T2" in ready, str(ready))

    sc.step("The event log tells the full story of the incident")
    _, out, _ = sc.orchard("doctor")
    sc.check(
        "doctor reports a healthy log after recovery",
        "Healthy" in out or "problem" not in out.lower(),
        out,
    )
