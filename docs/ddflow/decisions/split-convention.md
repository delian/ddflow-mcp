# Split convention: shared doc and baseline re-homes

Decided for `dx-split-doc-contention` (2026-11-01). A `<30k` split re-homes two kinds of
derived data, and the rule is that **a lane re-homes only files it leases**. The two files
every split used to hand-edit — `docs/ARCHITECTURE.md` (its module map) and a shared
constants register — serialized ~20 parallel lanes on two single-lease paths and lapsed
two lanes with uncommitted edits to exactly those files.

## The constants register is per-module

`docs/ddflow/constants-exemptions.toml` carries no `[knob]` / `[exempt]` tables. They live
one file per module under `docs/ddflow/constants-exemptions.d/<dotted.module>.toml`;
`ddflow/services/constants_audit.py` (`load_register`) reads the header file and every
fragment together, so a lane that registers or re-homes a constant edits only its own
module's fragment, never a shared register. A fragment is deleted when its last constant
leaves; a module with none has no fragment. Verified byte-identical to the register it was
split from before this change (`ddflow services constants_audit`).

## The ARCHITECTURE module map has one owner

`docs/ARCHITECTURE.md`'s layer map and module map are hand-maintained prose (a module token
plus what it is *for*), so they are not derivable and are re-homed by **one consolidation
pass** after a split wave lands, not by each lane. A split lane does not hand-edit them;
it re-homes only its module's code and its constants fragment. The one place that changes
`docs/ARCHITECTURE.md` is the consolidation owner.

## Guard baselines already follow this shape

`tests/guard_baselines/<counter>/<module>.toml` were already per-module for the same reason
(a task lowers only its own module's count); `[lease] shared_globs` already covers generated
pins. This decision extends the same reasoning to the constants register and the
ARCHITECTURE module map, so N parallel splits stop contending on the same shared files.
