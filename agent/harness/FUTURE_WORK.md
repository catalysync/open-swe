# Harness — future work

Deferred improvements mined from how production teams run internal coding-agent
harnesses (Stripe Minions, Anthropic multi-agent research, Cognition "Don't Build
Multi-Agents", Shopify/Airbnb/Uber). Each item lists the source principle, the
gap, and a sketch. Ordered roughly by value-to-effort.

## Shipped already (for context)
- Token/cost telemetry (`cost.py`, bench `$`/tokens) — Stripe/Anthropic cost focus.
- Auto-applied lint/format fixes before the gate (`validator._autofix`) — Stripe.
- Context pre-hydration of relevant files (`retrieve.py`) — Stripe Toolshed.
- Reviewers receive the developer's own summary, not just the diff
  (`reviewers._dev_trace`) — Cognition principle 1 (share full traces).
- Subdir-scoped house rules (`projects.load_rules(touched=...)`) — Stripe scoped
  rule files (avoid context saturation).

## Deferred — durable infra (needs run/thread keying)

### 0a. Per-run cost attribution
`cost.py` is a process-global accumulator: correct for serial `bench` (resets per
case) but under `langgraph dev` serving concurrent runs every run's tokens pile
into one counter. Proper fix keys totals by `thread_id`/run (same shape as the
SqliteSaver work) and stashes start/stop snapshots in graph state. Deliberately
deferred — a quick thread-local swap is only partially correct under the async
executor (one run can span threads), so it belongs with the durable-state work.

### 0b. SqliteSaver durable checkpointer (ADR 0005 / 0007)
Standalone runs use in-memory state; `langgraph dev` provides platform
checkpointing. Wire `AsyncSqliteSaver` for durable resume + time-travel when run
outside the platform.

## Deferred — improvements

### 1. LLM-as-judge eval rubric + grow the suite (Anthropic)
`bench.py` scores deterministic gates only. Add an LLM-judge pass scoring 0.0–1.0
on accuracy / completeness / simplicity / test-quality, and grow `SUITE` from 2 to
~20 representative tasks. Anthropic: ~20 cases catches 30%→80% shifts; judge a
rubric in one call; evaluate END-STATE, not every turn. Pairs with cost telemetry
to report cost-per-quality.

### 2. Scale reviewer effort to diff size (Anthropic scaling rules)
Always spawning 2 reviewers is wasteful on a one-line change and thin on a large
diff. Gate the security reviewer on diff size / risk-pattern presence; consider a
3rd reviewer (tests/perf) for large diffs. Needs conditional fan-out (the current
fan-out is static edges — see `graph.py` `_reviews_entry`).

### 3. Cost-aware retry budget (Stripe)
Stripe caps at ~1 CI retry "to avoid indefinite loops due to token/compute cost";
we do `MAX_RETRIES=3` (nodes.py). Now that cost is tracked, make the budget
cost-aware: stop retrying once spend crosses a per-run ceiling even if < 3.

### 4. Blueprint library — per-task-family graphs (Stripe)
Stripe authors custom "blueprints" (state machines of deterministic + agent nodes)
per task family, e.g. codebase migrations. We have two fixed graphs (`agent`,
`mine`). Add more named graphs for recurring shapes (dependency bump, framework
migration, test-backfill) rather than forcing everything through the build graph.

### 5. Context compression / external memory (Cognition + Anthropic)
Long tasks overflow context. Cognition: a (possibly fine-tuned) model compresses
history into key decisions/events; Anthropic: summarize completed phases to
external memory and persist the plan (200k truncation). Today we lean on claude
-p native compaction only. Add an explicit summarize-to-file step for long runs.

### 6. Filesystem handoff for large outputs (Anthropic)
Subagents/sections currently pass full content through graph state messages.
Have them write artifacts to files and pass lightweight references instead — less
token overhead, no loss across stages. Relevant to `mine` sections + reviewers.

### 7. Minimal default toolset (Stripe)
Minions get an intentionally small tool subset out of ~500. Hard to control
precisely via `claude -p` (it owns its built-ins), but worth revisiting if/when we
expose MCP tools — expose a curated subset by default, not everything.

## References
- Stripe Minions, parts 1 & 2 — stripe.dev/blog
- Anthropic, "How we built our multi-agent research system" — anthropic.com/engineering
- Cognition, "Don't Build Multi-Agents" — cognition.ai/blog
