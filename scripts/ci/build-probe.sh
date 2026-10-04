#!/usr/bin/env bash
# The wheel must exist, install, run, and carry its templates.
#
# `uv build` succeeding proves the metadata parses. It does NOT prove the console script
# resolves or that the shipped templates made it in, and a wheel missing its templates
# fails at the first `ddflow help` -- on a user's machine.
#
# Run by CI (quality job) and by the pre-push hook, so the two cannot drift.
set -euo pipefail
# Hermetic: an ambient PYTHONPATH (an agent gate environment pins one at the source tree)
# would put the checkout ahead of the wheel on sys.path and defeat the probe.
unset PYTHONPATH PYTHONHOME
root=$(cd "$(dirname "$0")/../.." && pwd)
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT

uv build --quiet --project "$root" --out-dir "$out/dist"
uv venv --quiet "$out/probe"
uv pip install --quiet --python "$out/probe/bin/python" "$out"/dist/*.whl
# From the scratch directory, not the checkout: `python -c` puts the working directory on
# sys.path, and run from the repository root it imports the SOURCE tree -- which always has
# its templates -- instead of the wheel this probe exists to check.
cd "$out"
# The console script a user types, not `python -m ddflow`: `__main__` can import fine while
# the [project.scripts] target is misspelled, and only the shim would say so.
probe/bin/ddflow --version
probe/bin/python -c 'from ddflow.infra.paths import templates_dir; import ddflow, sys; d=templates_dir(); m=[n for n in ("companions.toml","prompts") if not (d/n).exists()]; sys.exit(f"missing package data: {m}" if m else 0) if "site-packages" in ddflow.__file__ else sys.exit(f"probed the source tree, not the wheel: {ddflow.__file__}")'
