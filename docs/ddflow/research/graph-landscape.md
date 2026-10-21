# Graph engineering for AI agents (as of 2026-10-19) -- partial, extended below
Legend: CONFIRMED = read via fetch of the source abstract/page this session; CLAIMED = search snippet / vendor / secondary; PRIOR = my background knowledge, not re-verified.

## 2. GraphRAG / KG memory
- Zep/Graphiti (arXiv 2501.13956, Jan 2025) CONFIRMED abstract: DMR 94.8% vs MemGPT 93.4%; LongMemEval "up to 18.5% accuracy gain", "90% lower latency" vs baseline. Vendor-authored paper; DMR gap is 1.4 pts (small). Cost: LLM extraction on every ingest (PRIOR).
- "When to use Graphs in RAG" (Xiang et al., arXiv 2506.05690, ICLR 2026) CONFIRMED (abstract: GraphRAG 'frequently underperforms vanilla RAG on many real-world tasks'; benchmark across fact retrieval/reasoning/summarization/creative) ; numbers CLAIMED via snippet: GraphRAG often UNDERperforms vanilla RAG (e.g. -13.4% on NaturalQuestions), +4.5% on HotpotQA multi-hop, ~2.3x latency. Wins only for multi-hop/global/sensemaking over richly interconnected corpora. Source: https://arxiv.org/abs/2506.05690
- Vendor benchmarks (FalkorDB 71.5 vs 55.4 on GraphRAG-Bench; 89% vs 67% claims; Lettria, Fluree): CLAIMED, vendor-run, treat as marketing.
- Microsoft GraphRAG / LightRAG / Mem0-graph / Cognee: PRIOR only: graph build = many LLM calls per chunk (indexing cost orders above vector), staleness requires re-extraction/invalidation (Graphiti addresses with bi-temporal edges).
- Applicable to ddflow? No for NL memory: ddflow records are already typed, structured, event-sourced; recall via FTS/similarity is enough. Maybe edges decision<->globs<->items as plain stdlib adjacency.

## 3. Code graphs
- Agent Retrieval Bench (HF paper 2607.24882, Jul 2026) CONFIRMED via fetch: RepoMap best BCY@8k (0.3788) and dominant on trace2code; embeddings (Qwen3-8B) better on code2test, comment2context; RRF fusion of both raised MRR 0.2296->0.2713, Recall@20 0.7070->0.7331. => structure and semantics complementary; no single winner.
- RepoGraph (arXiv 2410.14684, ICLR 2025) CLAIMED via snippet: +2.3..2.7 pts absolute SWE-bench resolve rate, avg 32.8% relative; plug-in to Agentless/RAG. Modest absolute gain.
- RepoAtlas (arXiv 2609.16936), ARISE (2605.03117): exist (snippet only); claim better resolve rate with fewer tokens. CLAIMED.
- Aider repo map, tree-sitter, SCIP/LSIF, CodeQL, Sourcegraph: PRIOR; not verified with numbers this session.
- Applicable to ddflow? Partly: file-glob overlap/impact between tasks is a cheap import-graph problem (Python ast stdlib fits D-graph-stdlib); full SCIP/CodeQL no.


- Aider repo map (aider.chat/docs/repomap.html) CONFIRMED: files are nodes, dependency edges, graph-ranking algorithm picks most-referenced identifiers; default budget 1k tokens (--map-tokens). Claimed benefits: API visibility, file discovery, context efficiency. No published ablation numbers on that page.

## 1. Workflow graphs / durable execution
- LangGraph persistence docs CONFIRMED: checkpointer snapshots graph state per thread; resume by thread_id; supports human-in-the-loop (pause, await input, resume), time-travel, fault tolerance; separate store for cross-thread memory. The persistence page did NOT cover parallel Send fan-out (PRIOR: Send/map-reduce exists; Temporal-style durable execution requires deterministic replay and idempotent activities).
- What they get right (consistent across docs): explicit state, checkpoint at step boundaries, interrupt edges as first-class. ddflow already has: event log = checkpoint, leases, gates = HITL/verification edges.
- Applicable to ddflow? Already convergent; ddflow's event-sourcing is stronger than snapshot checkpointing (replay/audit). Don't adopt LangGraph; borrow time-travel (replay to point) if not present.

## 4. Context engineering / protocols
- Anthropic 'Effective context engineering' CONFIRMED: just-in-time retrieval via lightweight references, compaction, structured note-taking to external files, sub-agents returning 1-2k token summaries; context as finite resource; 'do the simplest thing that works'. No explicit graph content -- structure advocated is sections (XML/Markdown) and references.
- A2A, MCP resources: not fetched (PRIOR only). ddflow already exposes MCP.
- Applicable to ddflow? Yes in spirit: briefs should be pointers + ranked records (ddflow_brief already does this).

## 5. What does not work / overhyped
- GraphRAG not uniformly better (Xiang et al., ICLR 2026, CONFIRMED qualitatively); vendor benchmarks CLAIMED and self-serving.
- Multi-agent failures (MAST, arXiv 2503.13657) CONFIRMED: 14 failure modes in 3 classes (system design, inter-agent misalignment, task verification) from 1600+ traces over 7 frameworks -> verification gates are where failures cluster; supports ddflow's gate design.
- Code graph gains are modest and task-specific (RepoGraph ~+2.5 abs pts CLAIMED; Agent Retrieval Bench CONFIRMED complementary not dominant).
- Not verified this session: Microsoft GraphRAG cost numbers, LightRAG, Mem0 (their paper claims), Cognee, SCIP, CodeQL, Sourcegraph, test-selection numbers, A2A.
