# Merge drivers for the files every task touches

Many tasks edit the same few files one line at a time. These get a git merge driver, so the
merge is clean and the probe (`git merge-tree` honours `.gitattributes` drivers) sees the same
result as the real merge.

| Kind of file | Driver | Declared in |
| --- | --- | --- |
| Append-only logs (changelog, research log) | `merge=union` | `[lease].append_only_globs` |
| Sorted one-entry-per-line baselines | `merge=union`, then `pins drivers --check` | `[pins].baseline_globs` |
| Pins, golden snapshot indexes | `merge=pinsregen` (keep ours, regenerate after the merge) | `[pins]` recipes |
| `*.py` (opt-in evaluation) | `merge=mergiraf` | `[pins].mergiraf` |

## Setup (with consent)

- `ddflow pins drivers` (MCP `ddflow_pins document=drivers`) prints the exact lines it would add
  to `.gitattributes` and the git config keys, and writes nothing.
- `ddflow pins drivers --install` (MCP `install=true`) writes them. It reuses the pin driver and
  post-merge hook of `pins regenerate --install`; it is not a second driver.
- `ddflow pins drivers --check` verifies the union result: every baseline sorted, no repeated
  entry, no conflict markers. Exit 1 names each file and the fix.

## Mergiraf (opt-in evaluation)

Off by default. With `[pins].mergiraf = true`, `pins drivers` reports whether the
`mergiraf` binary is on PATH and whether git is at least `mergiraf_min_git` (2.44; the
recommended setup uses `%P`). A missing binary or an older git is reported unavailable and
nothing is written; ddflow never installs Mergiraf. Measure it by replaying past merges and
keep it only if it resolves more than `mergiraf_keep_pct` (10) percent of the textual
conflicts.

Knobs: `[pins].baseline_globs`, `mergiraf`, `mergiraf_globs`, `mergiraf_min_git`,
`mergiraf_keep_pct`; each settable with `ddflow config --set`, the environment, the local layer
and the committed file.
