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
            w = rest[i]
            if w in ("--kind", "--state"):
                if i + 1 >= len(rest):
                    return []  # a flag without its value: bad arguments print nothing
                if w == "--kind":
                    kind = rest[i + 1]
                else:
                    state = rest[i + 1]
                i += 2
            else:
                words.append(w)
                i += 1
        if len(words) > 1:
            return []
        if verb != "ids" and (kind is not None or state is not None):
            return []
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
