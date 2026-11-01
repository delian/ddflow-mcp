# Adapters: home, migration pace, family order, layer reconciliation

Decision document for task `ADP-decide` in phase `P-adapters`. It restates the
decision records below; the records in the event log are the source of truth
(`ddflow decision show D-adapters-answers` and `D-adapters-answers-2`, which
completes it).

## D-adapters-answers — Adapters: new ddflow/adapters package, strangler with ratchets family by family, census then http first, layer reconciliation folded into ADP-model

status **accepted** · decided by operator

**Decision.** The operator answered `ADP-decide`'s questions on 2026-10-31. The
answers are recorded verbatim in `D-adapters-answers`; this file is the record that
decision names.

| # | Question | Default | Operator's choice |
|---|---|---|---|
| 1 | Adapter home | `ddflow/infra/<family>` | a new `ddflow/adapters` package |
| 2 | Migration pace | strangler: migrate a family when its task runs, ratchet baselines only shrink | strangler with ratchets, family by family |
| 3 | Family order | http, sqlite, toml, filesystem/shutil, templates, third-party singles, clock/env | census first, then http, then the default order |
| 4 | Layer-contract dependency | layering edge to `B-arch-layer-contracts` | drop the edge; fold layer reconciliation into `ADP-model` |

### 1. Adapter home: a new `ddflow/adapters` package

The default was keeping each family in its `ddflow/infra/<family>` home. The operator
chose a **new `ddflow/adapters` package** instead.

`ddflow.adapters` sits at the **bottom of the layer contract**: core code uses only our
own adapters, so the package is importable by every layer while importing no other
`ddflow` layer. A port (`Protocol`), its default implementation and its registry live
together in one package (ports next to their adapters, not in core); fakes live in
`ddflow/adapters/fakes` (shipped), with a ratchet requiring every port to have a fake
(`D-adapters-answers-2`). The existing infra homes (`proc`, `git`, `fsio`, `tomlcfg`,
`digest`) migrate under it behind compatibility re-exports until their ratchet baselines
are empty.

### 2. Migration pace: strangler with ratchets, family by family

The default was the strangler over a big-bang move; the operator confirmed the
**strangler**. Each family migrates when its own task runs; a ratchet baseline's ignore
list only ever shrinks — a guard count can never go back up (strangler with ratchets,
`D-unify`). There is no big-bang restructure, and no observable behaviour change for
callers.

### 3. Family order: census, then http, then the default order

The default order is http, sqlite, toml, filesystem/shutil, templates, third-party
singles, clock/env. The operator chose the default, prefixed by the **census**: run the
dependency census first (`ADP-census`, driven by the probe of `R-adapter-boundaries`) so
every family is enumerated before migration, then take **http** first, then the rest of
the default order.

### 4. Layer reconciliation: folded into `ADP-model`

The default staged a separate layering edge to `B-arch-layer-contracts`. The operator
chose to **drop that edge and fold the layer reconciliation into `ADP-model`** instead:
`ADP-model` no longer waits on `B-arch-layer-contracts` and also produces the one
reconciled layer contract.

### 5. Completing answers: ManagedFile and config formats (`D-adapters-answers-2`)

`D-adapters-answers-2` completes `D-adapters-answers` with two further operator answers:

- **ManagedFile** backs up next to the file, with consent once per file kind: a
  `.ddflow-bak` copy before the first write, ownership markers on every ddflow-owned key,
  consent once per kind of file (for example user-level Vibe hooks), not per write.
- **Config formats** are TOML-only for ddflow's own config; JSON, JSONC and YAML are
  handled only in `ManagedFile` for other tools' files.

## Conformance

The conformance clause lives in the adapter task bodies, not in `D-adapters-answers`
itself. `ADP-decide` / `ADP-model` carry the clause (r-reuse-core, r-small-files,
r-everything-configurable, `D-unify` strangler with ratchets):

- one implementation per function: a port (`Protocol`), one default implementation in its
  home module, and a fake for tests;
- baseline ignore lists only shrink — a guard count can never go back up;
- no observable behaviour change for callers;
- files under 20 KB.

`ADP-migrate-homes` adds the same-change home-entry rule: an existing home's import-linter
`home` entry is updated in the same change that moves it (entries only shrink), tests
first, no behaviour change.

## Evidence

- `R-adapter-boundaries` (CONFIRMED, 2026-10-31): the dependency census — module,
  importing files, home, violations — that one home contract per family and the
  migration order run against; its falsifier leaves a port-less family a documented home
  module whose ratchet only forbids new importers.
- `D-unify` (strangler + ratchets) carries the pace rule into `D-unify-r2`.

## Done when

This file exists and names `D-adapters-answers` and `D-adapters-answers-2` (it does).
The decision records themselves are filed in the event log and returned by `ddflow
recall` / `ddflow decision show`.
