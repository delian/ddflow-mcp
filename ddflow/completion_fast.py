"""Entry for the shell-completion hook: answer from the index, import nothing heavy.

    python -m ddflow.completion_fast ids [--kind K] [--state S] [PREFIX]
    python -m ddflow.completion_fast phases|kinds|states [PREFIX]

Prints one candidate per line and always exits 0; no index, a locked or corrupt
database, or bad arguments print nothing. Standard library only: `ddflow.api` (about
0.8 s to import) must never be reached from here (tests/test_completion_ids.py
holds the import budget).
"""

from __future__ import annotations

import sys

from .services import completion_ids as CI


def complete(argv: list[str]) -> list[str]:
    """Candidates for ``argv``; never raises."""
    try:
        if not argv:
            return []
        verb, rest = argv[0], argv[1:]
        kind = state = None
        words: list[str] = []
        i = 0
        while i < len(rest):
            if rest[i] in ("--kind", "--state") and i + 1 < len(rest):
                if rest[i] == "--kind":
                    kind = rest[i + 1]
                else:
                    state = rest[i + 1]
                i += 2
            else:
                words.append(rest[i])
                i += 1
        prefix = words[0] if words else ""
        if verb == "ids":
            return CI.complete_ids(prefix, kind, state)
        if verb == "phases":
            return CI.phases(prefix)
        if verb in ("kinds", "states"):
            vals = CI.kinds() if verb == "kinds" else CI.states()
            return [v for v in vals if v.startswith(prefix)]
    except Exception:
        pass
    return []


def main(argv: list[str] | None = None) -> int:
    out = complete(list(sys.argv[1:] if argv is None else argv))
    if out:
        sys.stdout.write("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
