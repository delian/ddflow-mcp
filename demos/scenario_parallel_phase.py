"""Scenario 1 — "implement phase P1" with two agents working in parallel.

The invented project is a URL shortener. Phase P1 has four tasks whose dependency and
file-glob structure is chosen so that the interesting things actually happen:

    T1 storage   (writes shortener/store.py)      -- independent
    T2 encoder   (writes shortener/encode.py)     -- independent
    T3 api       (writes shortener/api.py)        -- needs T1 and T2
    T4 store-cli (writes shortener/store_cli.py)  -- independent, but its globs
                                                     OVERLAP T1's, so it must not run
                                                     beside T1

So the scheduler must: start T1 and T2 together, withhold T3 on dependencies, and
withhold T4 on a *file conflict* — three different reasons, which the run prints.
"""

from __future__ import annotations

from pathlib import Path

from harness import Scenario

SCAFFOLD = {
    "README.md": "# tinyurl\n\nA URL shortener.\n",
    "shortener/__init__.py": "",
    "tests/__init__.py": "",
    ".gitignore": """
        __pycache__/
        *.pyc
        .pytest_cache/
    """,
    "run_tests.sh": """
        #!/bin/sh
        exec python3 -m pytest tests/ -q
    """,
}


def run(sc: Scenario) -> None:
    sc.head("SCENARIO 1 — a phase implemented by two agents in parallel")
    sc.make_repo("tinyurl", SCAFFOLD)

    sc.step("Initialise Orchard and declare the phase's work queue")
    sc.orchard("init")
    # Commit the setup, as a real operator does: `init` touches tracked files
    # (.gitignore, .gitattributes) and an uncommitted change there leaves the primary
    # checkout dirty, which `orchard merge` refuses.
    sc.git("add", "-A")
    sc.git("-c", "user.email=a@b", "-c", "user.name=t", "commit", "-qm", "orchard: adopt")
    sc.write(
        ".orchard/gates.toml",
        """
        [gate.unit_tests]
        command = "python3 -m pytest tests/ -q"

        [gate.standards]
        command = "python3 -m compileall -q shortener"
    """,
    )
    sc.orchard(
        "phase",
        "add",
        "P1",
        "--title",
        "Core shortening service",
        "--body",
        "Storage, encoding and the HTTP surface.",
    )
    tasks = [
        ("P1.T1", "persistent store", "", "shortener/store.py,tests/test_store.py"),
        ("P1.T2", "base62 encoder", "", "shortener/encode.py,tests/test_encode.py"),
        ("P1.T3", "HTTP api", "P1.T1,P1.T2", "shortener/api.py,tests/test_api.py"),
        ("P1.T4", "store maintenance CLI", "", "shortener/store*.py"),
    ]
    for tid, title, needs, globs in tasks:
        sc.orchard(
            "task",
            "add",
            tid,
            "--phase",
            "P1",
            "--title",
            title,
            *(["--needs", needs] if needs else []),
            "--globs",
            globs,
            quiet=True,
        )
    sc.note(
        "T4's globs (shortener/store*.py) deliberately overlap T1's "
        "(shortener/store.py) — two agents editing one file is the failure this "
        "system exists to prevent, so the scheduler must catch it."
    )

    sc.step("Ask what may start now — before anyone has claimed anything")
    plan = sc.jorchard("next", "--phase", "P1")
    ready = [r["id"] for r in plan["ready"]]
    sc.check(
        "T1, T2 and T4 are all offered (nothing is claimed yet)",
        set(ready) == {"P1.T1", "P1.T2", "P1.T4"},
        f"got {ready}",
    )
    blocked = {b["item"]: b for b in plan["blocked"]}
    sc.check(
        "T3 is withheld on its dependencies",
        blocked["P1.T3"]["reason"] == "deps",
        str(blocked.get("P1.T3")),
    )
    sc.check(
        "the critical path is reported as T1/T2 → T3",
        plan["critical_path"][-1] == "P1.T3",
        str(plan["critical_path"]),
    )

    sc.step("Agent ALPHA claims T1; agent BETA claims T2 — genuinely in parallel")
    sc.orchard("claim", "P1.T1", agent="alpha")
    sc.orchard("claim", "P1.T2", agent="beta")
    st = sc.jorchard("show", "P1.T1")
    wt_alpha = st["worktree"]
    wt_beta = sc.jorchard("show", "P1.T2")["worktree"]
    sc.check(
        "alpha and beta got DIFFERENT worktrees", wt_alpha != wt_beta, f"{wt_alpha} vs {wt_beta}"
    )
    sc.check(
        "each worktree is a real git checkout on its own branch",
        Path(wt_alpha).joinpath(".git").exists() and Path(wt_beta).joinpath(".git").exists(),
    )

    sc.step("A third agent GAMMA tries to take T4 — which overlaps T1's files")
    code, _, err = sc.orchard("claim", "P1.T4", agent="gamma", expect=3)
    sc.check("the claim is REFUSED with exit 3, not silently allowed", code == 3)
    sc.check(
        "the refusal names the overlapping file pattern and the holder",
        "store" in err and "alpha" in err,
        err.strip()[:200],
    )
    sc.note(
        "This is the whole point: gamma is told exactly why, so it re-orders "
        "instead of blocking — and the two agents never touch one file."
    )

    sc.step("Both agents implement their tasks in their own worktrees")
    sc.write(
        "shortener/store.py",
        '''
        """Durable slug -> url mapping."""
        import json, pathlib, threading

        class Store:
            def __init__(self, path):
                self.path = pathlib.Path(path); self._lock = threading.Lock()
                if not self.path.exists(): self.path.write_text("{}")

            def _read(self): return json.loads(self.path.read_text() or "{}")

            def put(self, slug, url):
                with self._lock:
                    d = self._read()
                    if slug in d and d[slug] != url:
                        raise KeyError(f"slug {slug!r} already maps elsewhere")
                    d[slug] = url
                    self.path.write_text(json.dumps(d))
                return slug

            def get(self, slug): return self._read().get(slug)
    ''',
        repo=Path(wt_alpha),
    )
    sc.write(
        "tests/test_store.py",
        """
        import pytest
        from shortener.store import Store

        def test_roundtrip(tmp_path):
            s = Store(tmp_path / "d.json")
            s.put("abc123", "https://example.com")
            assert s.get("abc123") == "https://example.com"

        def test_collision_is_refused(tmp_path):
            s = Store(tmp_path / "d.json")
            s.put("abc123", "https://a.example")
            with pytest.raises(KeyError):
                s.put("abc123", "https://b.example")
    """,
        repo=Path(wt_alpha),
    )
    sc.write(
        "shortener/encode.py",
        '''
        """Base62 slug encoding."""
        ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

        def encode(n: int, width: int = 6) -> str:
            if n < 0: raise ValueError("n must be non-negative")
            out = ""
            while n:
                n, r = divmod(n, 62); out = ALPHABET[r] + out
            return (out or "0").rjust(width, "0")

        def decode(s: str) -> int:
            n = 0
            for ch in s.lstrip("0") or "0":
                n = n * 62 + ALPHABET.index(ch)
            return n
    ''',
        repo=Path(wt_beta),
    )
    sc.write(
        "tests/test_encode.py",
        """
        import pytest
        from shortener.encode import encode, decode

        @pytest.mark.parametrize("n", [0, 1, 61, 62, 3843, 999999])
        def test_roundtrip(n):
            assert decode(encode(n)) == n

        def test_width_is_padded():
            assert len(encode(1)) == 6

        def test_negative_is_refused():
            with pytest.raises(ValueError):
                encode(-1)
    """,
        repo=Path(wt_beta),
    )
    sc.commit_in(Path(wt_alpha), "P1.T1: persistent slug store")
    sc.commit_in(Path(wt_beta), "P1.T2: base62 encoder")
    sc.check(
        "each worktree has exactly one new commit",
        sc.git("rev-list", "--count", "main..HEAD", repo=Path(wt_alpha)) == "1",
    )

    sc.step("Run the gate pipeline for T1 — tests are REAL and actually execute")
    for gate in ("research", "rules", "implement"):
        sc.orchard(
            "gate", "record", "P1.T1", gate, "--outcome", "passed", "--evidence", "done", quiet=True
        )
    code, out, _ = sc.orchard("gate", "run", "P1.T1", "unit_tests")
    sc.check(
        "the real pytest run passed and was recorded with evidence",
        "PASSED" in out.upper(),
        out[-300:],
    )
    ev = sc.jorchard("show", "P1.T1")["gates"]["unit_tests"]["evidence"]
    sc.check(
        "the evidence carries the command, exit code and an output digest",
        ev["exit"] == 0 and ev["command"] and ev["output_digest"],
        str(ev),
    )

    sc.step("A reviewer is UNAVAILABLE — the moment of truth for the whole design")
    sc.orchard(
        "gate",
        "record",
        "P1.T1",
        "critic",
        "--outcome",
        "unavailable",
        "--reason",
        "review endpoint returned 503",
    )
    _, out, _ = sc.orchard("gate", "status", "P1.T1")
    sc.check("the unavailable critic is NOT counted as a pass", "[?] critic" in out, out)
    sc.check("the gap is stated explicitly in the status output", "not a pass" in out, out)
    code, _, err = sc.orchard("complete", "P1.T1", "--model", "claude-opus-5", expect=3)
    sc.check("completion is REFUSED with exit 3", code == 3)
    sc.check(
        "the refusal lists EVERY unmet condition at once, not just the first",
        "independence" in err and "merge" in err,
        err[:400],
    )
    sc.note(
        "Both blockers are reported together: an agent that has to discover them "
        "one refusal at a time reaches for --force."
    )

    sc.step("A different-family reviewer runs; now the task may complete")
    sc.orchard(
        "gate",
        "record",
        "P1.T1",
        "rubber_duck",
        "--outcome",
        "passed",
        "--evidence",
        "checked collision path; no findings",
        "--model",
        "gemini-2.5-pro",
    )
    for g in ("standards", "bug_hunt", "dedupe"):
        sc.orchard(
            "gate", "record", "P1.T1", g, "--outcome", "passed", "--evidence", "clean", quiet=True
        )
    sc.orchard("merge", "P1.T1")
    sc.orchard("complete", "P1.T1", "--model", "claude-opus-5")
    sc.check("T1's code is now on main", "class Store" in sc.git("show", "main:shortener/store.py"))

    sc.step("T3 was blocked on T1+T2; it must still be blocked on T2 alone")
    plan = sc.jorchard("next", "--phase", "P1")
    blk = {b["item"]: b for b in plan["blocked"]}
    sc.check(
        "T3 remains blocked, now waiting only on T2",
        blk["P1.T3"]["waiting_on"] == ["P1.T2"],
        str(blk.get("P1.T3")),
    )
    sc.check(
        "T4 is now claimable — the conflict left with T1's lease",
        "P1.T4" in [r["id"] for r in plan["ready"]],
        str(plan["ready"]),
    )

    sc.step("Finish T2, and confirm T3 unblocks automatically")
    for g in ("research", "rules", "implement", "standards", "bug_hunt", "dedupe"):
        sc.orchard(
            "gate", "record", "P1.T2", g, "--outcome", "passed", "--evidence", "ok", quiet=True
        )
    sc.orchard("gate", "run", "P1.T2", "unit_tests", agent="beta")
    sc.orchard(
        "gate",
        "record",
        "P1.T2",
        "rubber_duck",
        "--outcome",
        "passed",
        "--evidence",
        "padding path checked",
        "--model",
        "gpt-5",
        quiet=True,
    )
    # `gates.require_outcome`: a gate left silent blocks completion, so the critic has
    # to be recorded as UNAVAILABLE rather than simply not run. That is the whole
    # point — "nobody ran it" and "it found nothing" must not look the same.
    sc.orchard(
        "gate",
        "record",
        "P1.T2",
        "critic",
        "--outcome",
        "unavailable",
        "--reason",
        "no critic endpoint configured in this demo repository",
        quiet=True,
    )
    sc.orchard("merge", "P1.T2", agent="beta")
    sc.orchard("complete", "P1.T2", "--model", "claude-opus-5", agent="beta")
    ready = [r["id"] for r in sc.jorchard("next", "--phase", "P1")["ready"]]
    sc.check("T3 became ready the moment its last dependency landed", "P1.T3" in ready, str(ready))
    sc.note(
        "No one told Orchard to unblock T3 — readiness is computed from the log, "
        "so it cannot drift from what actually shipped."
    )
