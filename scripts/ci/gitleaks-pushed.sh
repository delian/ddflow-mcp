#!/usr/bin/env bash
# gitleaks over the commits a push would publish -- the pre-push half of CI's Secrets step.
#
# At pre-push nothing is staged: a secret already committed is what the push publishes, so
# this scans commits, not the index. pre-commit exports the pushed range as
# PRE_COMMIT_FROM_REF..PRE_COMMIT_TO_REF. It exports NO range when the push carries a root
# commit (a repository's first push, an orphan branch), and a manual run has none either.
#
# The fallback must never be a range that names a missing ref: gitleaks hands the range to
# `git log`, and an unresolvable one scans "0 commits" and exits 0 -- a silent pass. So:
# what is not yet on origin/main when that exists, else the whole history of the tip.
set -euo pipefail
to=${PRE_COMMIT_TO_REF:-HEAD}
if [ -n "${PRE_COMMIT_FROM_REF:-}" ]; then
    range="$PRE_COMMIT_FROM_REF..$to"
elif git rev-parse --quiet --verify origin/main >/dev/null; then
    range="origin/main..$to"
else
    range="$to"
fi
exec gitleaks git --no-banner --redact --config .gitleaks.toml --log-opts="$range"
