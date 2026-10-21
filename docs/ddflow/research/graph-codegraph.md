# Code graph for ddflow: go/no-go (research, 2026-10-19)

## Finding zero
ddflow ALREADY has the cheap graph: services/testreach.py (stdlib ast; Graph.importers, build, reach, hubs, facade re-export handling) and services/testselect.py (changed tests, conftest scope, importers <=2 hops cut at hubs (>=100 importers), file-name stem, GOVERNS globs, changed-identifier spelling, package-listing, data-file readers). testscope.py applies it only to bugfix or <150-line tasks, max 100 files; ci gate/merge queue run the whole suite. A `recall replay` over main merges exists (tests/test_testselect_recall.py). So the question is not "build a graph" but "persist it, speed it, widen its uses".

## Measurements (probe: /tmp/claude-1001/research/graph_probe.py, read-only)
- 1470 .py files (628 ddflow/, 810 tests), 8107 import edges (relative imports, from-pkg-import-module, lazy in-function imports via ast.walk). 33 files use importlib/__import__.
- Build cold: own ast.walk probe 4.3 s; production testreach.build 5.4 s; parse alone 2.0 s. Rebuilt on every select() call (my 10-merge replay took ~100 s for 20 selects). Serialized graph: 119 KB pickle (JSON ~ same order).
- Unbounded reverse-import closure is useless as a selector: median src module reaches 459/810 tests, p90 747, mean 447. Hubs by in-degree: services/__init__ 529, config.py 388, core/model.py 348, infra/log.py 326.
- Blast radius: core/records.py: 41 direct importers, 404 transitive modules, 747/810 tests (92%). api/review.py: 40 direct, 90 transitive modules, 452/810 tests (56%).
- 10 random recent merges (src+test both changed, <60 files; graph at HEAD, git/idents stubbed so approximate), proxy truth = tests changed in the merge (they are a lower bound on relevant tests, and "tests the author touched" are mostly name/import-near):
  unbounded closure: recall 1.00, reach mean 576 tests (71%).
  existing selector (src files only, no test files fed in): recall 0.92 (misses: 987b4ad2 0.50, 42e7b5a0 0.67), reach mean 77 tests (9.5%), none triggered full-suite.
  Precision vs changed tests is meaningless (1-7 changed vs 22-156 reached); the real precision question needs failing-test ground truth from CI history.

## Literature (searched; * = from memory, not re-verified)
- Meta predictive test selection (Machalica et al., ICSE-SEIP 2019): ML on history, >99.9% of faulty changes and >95% of individual failures still caught while running 1/3 of the tests that transitively depend on the change; infra cost halved. https://arxiv.org/abs/1810.05286
- Ekstazi (Gligoric 2015)*: dynamic file-level deps, safe; ~32% of RetestAll time on average. STARTS (Legunsen 2016)* static class-level deps: safe w.r.t. its static model but over-selects (~20-30% more than Ekstazi), static misses reflection.
- Google TAP (Memon 2017 "Taming Google-scale continuous testing")*: build-graph-dependency selection, so every test reachable from the change runs; savings come from skipping non-reachable targets plus batching and culprit finding, not from a smarter filter. Same lesson as our closure numbers: pure reachability on a hub-heavy graph does not shrink much.
- pytest-testmon*: coverage-based, per-test function/checksum fingerprints; catches dynamic imports, data files read, conftest; costs a coverage run and a sqlite DB (needs coverage dep, so not stdlib; sys.settrace/sys.monitoring could do it stdlib-only but slow). pytest-picked*: git-status file-name based only (what testselect's name rule already does).
- Aider repo map: tree-sitter tags, symbol def/ref graph, personalized PageRank, binary-search to a token budget (default 1024 tokens). Context selection for agents, not tests. https://www.agentpatterns.ai/context-engineering/repository-map-pattern/
- SCIP (Sourcegraph): protobuf, human-readable symbol IDs, replaces LSIF whose in-memory graph encoding was slow and hard to make incremental; reported ~10x faster, ~4x smaller*. Needs language indexers (scip-python = pyright), not stdlib; overkill for 700 modules.

## Risks (why a pure import graph under-selects)
importlib/__import__ (33 files), registry-by-convention (services/registry, surfaces/registry, command and tool registration found by package walk), `__init__` re-exports (testreach handles names only), conftest fixtures (handled by directory rule), data/golden/TOML defaults read by path (handled by text spelling rule, partly), subprocess CLI tests (invisible), string-built names, config knobs, pyproject/import-linter contracts, TYPE_CHECKING-only imports (counted as edges; harmless over-select).
Safety net: (1) always-run guards list (DX-ci-always-run-guards: architecture, registry, docs-parity, import-linter, goldens) run on every selection; (2) any dynamic-import or registry file changed => widen to the registry's tests or full suite; (3) selection is a fast-feedback tool only; merge queue/phase end stay full (already so); (4) nightly/continuous recall replay against actual failures, alarm if recall <1.0 on failing tests; (5) stats: record per-run "selected tests that failed" and "full-run failures not selected" as events.

## Go/no-go
GO on a small persisted-graph upgrade; NO-GO on a new graph subsystem, SCIP, tree-sitter, or full transitive closure as the selector.
Value: speed (selector build 5.4 s -> ~0.3 s warm, per gate/CLI call), and accuracy mainly via the 2 observed recall misses (hop cap) and a reverse-closure "explain" for humans/agents (blast-radius report: records.py hits 92% of tests so it must say "run all").

## Minimal design
- Module core/graph (D-graph-stdlib: graphlib.TopologicalSorter for order/cycles; own BFS closure, SCC via Tarjan if cycle report needed); reuse testreach.Graph, do not fork.
- Data: `Graph{ fwd: dict[path, tuple[path]], rev: dict[path, frozenset[path]], dynamic: frozenset[path], exports/uses (existing) }`, JSON in `.ddflow/cache/importgraph.json` (derived, gitignored; <150 KB).
- Per-file cache keyed by git blob hash: `git ls-files -s '*.py'` (one call, ~20 ms) gives path->blob sha; re-parse only files whose sha differs; edges for unchanged files reused; reverse index rebuilt (ms). Whole-cache key = tree hash of ddflow/ + tests/ (`git rev-parse HEAD:ddflow HEAD:tests`) for a fast hit when nothing changed; worktree dirty files hashed with `git hash-object`. Version field + algorithm salt in the key.
- API: `load(tree) -> Graph`; `reach(graph, changed, max_hops, hubs) -> Reach` (existing); `blast(graph, path) -> {modules, tests, share}`; `affected_tests(graph, changed, policy)` returns (tests, widen_reason); `ddflow graph blast <path>` for agents.
- Policy tweak to test: hop cap 3 for non-hub chains and "dynamic-import file changed => widen"; evaluate with the existing recall replay on the last 200 merges using real CI failures, before changing defaults.
