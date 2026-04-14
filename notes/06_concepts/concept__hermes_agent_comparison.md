---
created: 2026-04-14T00:00:00Z
updated: 2026-04-14T00:00:00Z
tags: [comparison, hermes-agent, cognitive-ledger, agent-memory, architecture]
confidence: 0.85
source: tool
scope: dev
lang: en
---

# Cognitive Ledger vs Hermes Agent: Architectural Comparison

## Statement

The cognitive-ledger and NousResearch/hermes-agent represent two fundamentally
different approaches to agent memory and persistence. The cognitive-ledger is a
**memory-first system** (structured note corpus with retrieval), while hermes-agent
is a **full-stack autonomous agent** (execution runtime with memory as one subsystem).
They are complementary rather than competitive.

## Overview

| Dimension | Cognitive Ledger | Hermes Agent |
|-----------|-----------------|--------------|
| **Core identity** | Persistent memory system for AI agents | Self-improving autonomous AI agent |
| **Version** | — | 0.9.0 |
| **License** | — | MIT |
| **Language** | Python 3.11+ | Python 3.11+ |
| **Stars** | — | ~84k |
| **Scope** | Memory layer (plugs into any agent) | Full agent runtime (LLM loop + tools + memory + UI) |

## Architecture Comparison

### 1. Memory Model

**Cognitive Ledger** — Structured note corpus with multi-stage retrieval:
- Storage: Atomic markdown files with YAML frontmatter, organized by type
  (facts, preferences, goals, identity, concepts, open loops)
- Naming: `{type}__{slug}.md` in typed folders (`01_identity/` through `09_archive/`)
- Frontmatter: `created`, `updated`, `tags`, `confidence`, `source`, `scope`, `lang`, `status`
- Retrieval: 7 retrieval modes including BM25, semantic hybrid, compressed attention,
  progressive disclosure. Multi-stage pipeline: query expansion -> prefiltering ->
  BM25 shortlisting -> weighted scoring (lexical 15%, BM25 30%, tags 15%,
  scope 15%, recency 15%, confidence 10%)
- Capacity: Unbounded corpus; context-window-aware (boot context is compact ~1-2KB,
  on-demand retrieval for deeper queries)
- Feedback: Signal loop (retrieval_hit/miss, correction, affirmation, stale_flag)
  feeds back into scoring weights

**Hermes Agent** — Lightweight flat-file memory with character budgets:
- Storage: Two plain-text files (`MEMORY.md` and `USER.md`) in `~/.hermes/memories/`
- Format: Section-sign (§) delimited entries, no structured metadata
- Retrieval: Substring matching only (no ranking, no semantic search)
- Capacity: Hard character limits — MEMORY.md: 2,200 chars; USER.md: 1,375 chars
- System prompt injection: Memory snapshot frozen at session start, injected into
  system prompt. Mid-session mutations don't affect what the LLM sees until next session
- Persistence: Atomic temp-file-plus-rename with file locking

**Verdict**: The cognitive-ledger's memory system is orders of magnitude more
sophisticated. Hermes trades depth for simplicity — its memory fits in a system
prompt and never requires retrieval infrastructure. The cognitive-ledger can scale
to thousands of notes with ranked retrieval; hermes-agent caps at ~3,500 characters total.

### 2. Cross-Session Recall

**Cognitive Ledger**:
- Boot context loads identity + active loops + status (~1-2KB)
- On-demand retrieval across full corpus via CLI (`./scripts/ledger query`)
- Timeline (`timeline.md`) provides append-only audit trail
- Index (`index.json`) for fast lookup without loading full corpus
- Embedding-based semantic search (optional, via sentence-transformers)

**Hermes Agent**:
- Session search via FTS5 (SQLite full-text search) across past conversations
- Three-stage pipeline: FTS5 query -> session deduplication -> parallel LLM summarization
- Delegation chain resolution (child sessions map to parent conversations)
- Truncation around match positions (100KB window)
- Two modes: recent (metadata only, zero LLM cost) and search (LLM-summarized)

**Verdict**: Different strategies for different problems. The cognitive-ledger
distills durable knowledge into atomic notes (no raw transcripts); hermes-agent
indexes raw session transcripts and uses LLM summarization at query time. The
cognitive-ledger approach is more token-efficient at retrieval time; hermes-agent's
approach preserves more raw context but requires LLM calls to extract relevance.

### 3. Skill / Procedural Memory

**Cognitive Ledger**:
- Single `/notes` skill for Claude Code integration
- Skill protocol defined in `SKILL.md` with routing table (intent -> note type)
- Skills are static — they encode the protocol for using the ledger, not learned procedures
- No autonomous skill creation or improvement

**Hermes Agent**:
- Full skill lifecycle: create, invoke, patch (fuzzy-matched find-replace), edit (full rewrite)
- Skills stored as `SKILL.md` files in `~/.hermes/skills/` with categories
- Self-improvement loop: "If you used a skill and hit issues not covered by it, patch it immediately"
- Community sharing via Skills Hub (agentskills.io)
- 26+ built-in skill categories (research, software-development, creative, gaming, etc.)
- Security scanning on skill modifications

**Verdict**: Hermes-agent has a much richer skill system with autonomous creation
and self-improvement. The cognitive-ledger doesn't aim to be a skill system — it's
a memory substrate that skills (and agents) consume.

### 4. Agent Execution Loop

**Cognitive Ledger**:
- No agent loop — it's a library/CLI that agents invoke
- Operating protocol: Search -> Respond -> Persist -> Signal -> Report
- Relies on host agent (Claude Code, Codex, etc.) for the actual LLM interaction loop
- Cross-agentic by design: notes are agent-agnostic

**Hermes Agent**:
- Full `AIAgent` class with conversation loop (`run_conversation()`)
- Iteration budget (90 turns parent, 50 for subagents)
- Tool batching with parallel execution for read-only operations
- Interrupt mechanism for mid-loop cancellation
- Subagent delegation with isolated parallel workstreams
- Error classification, jittered backoff, fallback provider chains
- 200+ model support across multiple providers (OpenAI, Anthropic, OpenRouter, HF, etc.)

**Verdict**: Hermes-agent is a complete agent runtime; the cognitive-ledger is
not an agent at all. This is the fundamental architectural difference.

### 5. Context Management

**Cognitive Ledger**:
- Two-tier lookup: boot context (always loaded) + on-demand retrieval (query-driven)
- Identity notes receive score boost (+0.15)
- Progressive disclosure (top-3 get full rationale, rest compact)
- Token-aware design: `wc -w` estimates, compact indices, avoid loading full corpus

**Hermes Agent**:
- `ContextCompressor` with four-phase algorithm:
  1. Tool result pruning (replace old outputs with placeholders)
  2. Boundary protection (preserve head + tail, ~20K token tail budget)
  3. Structured summarization via auxiliary model (8 sections: goal, progress, decisions, etc.)
  4. Iterative updates (re-compression updates prior summaries, doesn't re-summarize from scratch)
- Trigger at 75% context capacity; protect first 3 and last 6 messages
- Plugin architecture for alternative compression engines

**Verdict**: Different problems. The cognitive-ledger manages what enters context
(retrieval gating); hermes-agent manages what stays in context (compression).
Both are necessary for a complete system.

### 6. Tool Ecosystem

**Cognitive Ledger**:
- CLI tools: `ledger query`, `ledger signal`, `ledger embed`, `ledger eval`, `ledger loops`
- Shell utilities: `rg`, `fd`, `git` for search and versioning
- A/B testing framework for retrieval quality
- Eval harness with YAML test cases

**Hermes Agent**:
- 40+ integrated tools: terminal, browser, file operations, code execution,
  vision, TTS, image generation, home automation, RL training
- MCP server integration
- Tool approval workflows for destructive operations
- Mixture-of-agents tool for ensemble reasoning
- Delegate tool for subagent orchestration

### 7. Maintenance & Self-Healing

**Cognitive Ledger** — "Electric Sheep" consolidation:
- Merge duplicate notes (semantic + lexical matching)
- Promote patterns into stable concepts/preferences
- Close stale open loops
- Update indices and timelines
- Surface conflicts as explicit open loops
- Automated via cron (`sheep-auto.sh`)

**Hermes Agent**:
- No equivalent consolidation system
- Memory is small enough (~3.5KB) that maintenance isn't needed
- Skills self-improve through patch/edit during use

### 8. Observability & Evaluation

**Cognitive Ledger**:
- Signal feedback loop (7 signal types)
- Retrieval eval framework (Hit@k, MRR, latency)
- A/B testing across git refs
- Timeline audit trail
- Score component breakdown per result

**Hermes Agent**:
- Token usage tracking and cost estimation
- Rate limit tracking
- Trajectory compression for training data generation
- Session logging to SQLite
- Insights module for usage analytics

### 9. Integration Surface

**Cognitive Ledger**:
- Claude Code (skill + hooks)
- Codex (AGENTS.md)
- Obsidian (bootstrap, import, watch, sync, daemon)
- Any agent via CLI/Python library
- TUI (Textual-based terminal interface)

**Hermes Agent**:
- Telegram, Discord, Slack, WhatsApp, Signal (via gateway)
- CLI with full TUI
- Web interface
- Docker, SSH, Daytona, Singularity, Modal backends
- MCP server mode
- ACP adapter
- Homebrew, Nix packages

## Synthesis: How They Could Complement Each Other

The two systems occupy different layers of the agent stack:

```
┌─────────────────────────────────────┐
│  Hermes Agent (execution runtime)   │  Agent loop, tools, LLM routing,
│  - conversation loop                │  context compression, multi-platform
│  - tool orchestration               │  access, skill system
│  - context compression              │
│  - multi-platform gateway           │
├─────────────────────────────────────┤
│  Cognitive Ledger (memory layer)    │  Structured persistence, ranked
│  - atomic note corpus               │  retrieval, consolidation, eval,
│  - multi-stage retrieval            │  cross-agent readability
│  - feedback signals                 │
│  - consolidation (Electric Sheep)   │
└─────────────────────────────────────┘
```

A hypothetical integration would replace hermes-agent's flat MEMORY.md/USER.md
with the cognitive-ledger's structured corpus and retrieval pipeline, implemented
as a `MemoryProvider` plugin. This would give hermes-agent:
- Typed, ranked memory with confidence scores
- Scalable corpus (thousands of notes vs ~3.5KB cap)
- Cross-agent portability (other agents can read the same ledger)
- Retrieval evaluation and A/B testing
- Consolidation to prevent memory drift

In return, the cognitive-ledger would gain:
- A full execution runtime with 40+ tools
- Multi-platform access (Telegram, Discord, etc.)
- Autonomous skill creation and self-improvement
- Context compression for long conversations
- Subagent delegation

## Key Philosophical Differences

| Principle | Cognitive Ledger | Hermes Agent |
|-----------|-----------------|--------------|
| **Memory philosophy** | Distill, structure, rank | Store raw, summarize on demand |
| **Persistence granularity** | Atomic notes (one idea per file) | Two flat files + session DB |
| **Agent coupling** | Agent-agnostic (library) | Self-contained runtime |
| **Self-improvement** | Consolidation refines memory quality | Skills self-patch during use |
| **Scaling strategy** | Retrieval pipeline handles growth | Character budgets cap size |
| **Transparency** | Score breakdowns, eval harness, signals | Token usage, cost tracking |
| **Design motto** | "Noise kills retrieval" | "Creates skills from experience" |

## Links

- [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)
- ../01_identity/ (cognitive-ledger identity layer)
