"""ddflow — a portable, agent-agnostic work-queue kernel for AI coding agents.

The whole system rests on one decision: **the append-only event log is the source of
truth**, and every other artefact (the SQLite index, the markdown boards, the lessons
search index, the session replay bundle) is a *derived projection* that can be thrown
away and re-folded from the log at any time.

Read `docs/ARCHITECTURE.md` for why, and `docs/RESEARCH.md` for the probes that
decided each choice.
"""

#: One of the SIX places the version is declared; `scripts/bump.sh` moves them together
#: and `tests/test_packaging.py::test_the_declared_versions_agree` holds them equal.
#: This one sat at 0.1.0 through the 0.1.1 release because neither knew it existed.
__version__ = "0.1.9"
