---
id: ADR-004
title: Use composable stream processing pipeline
status: ACCEPTED
date: 2026-03-25
decision_makers: [Abdelaziz Abdelrasol]
consulted: []
informed: []
confidence: HIGH
tags: [stream, pipeline, parsing, composable, state-machine]
related_decisions: [ADR-001, ADR-002, ADR-003, ADR-007]
supersedes: []
superseded_by: []
---

# ADR-004: Use composable stream processing pipeline

## Status

ACCEPTED

## Context and Problem Statement

AI coding agent CLIs (Claude Code, Codex, Gemini, Copilot, OpenCode) each produce JSONL output on stdout/stderr with fundamentally different schemas, field names, event types, and noise levels. Claude emits content_block_delta events, system messages, and tool_use events. Codex emits task progress events with different field names. Gemini, Copilot, and OpenCode each have their own formats.

orchcore must transform these heterogeneous streams into a unified event model (`StreamEvent`) that the rest of the system (AgentMonitor, UICallback, StallDetector) can consume without knowing which agent produced the output.

In the four source systems, stream processing is handled inconsistently:
- **Planora** (Python): Separate filter and parse functions, but tightly coupled to Claude's format
- **Articles** (Bash): `grep -v` for filtering, `jq` for parsing — only handles Claude
- **Finvault** (Bash): Similar `grep -v` + `jq` approach, extended for Codex
- **Raven** (Bash): Most sophisticated — has separate filter, parse, and stall detection stages, but implemented as inline Bash functions with global state

The common pattern across all four systems is a multi-stage pipeline: first filter out noise (most JSONL lines are irrelevant), then parse relevant lines into structured events, then track agent state based on events, and finally detect stalls when events stop arriving. However, each system implements these stages as a monolithic function or inline code, making it impossible to test, extend, or reuse individual stages.

### Business Context

- Raw JSONL output from agent CLIs has extremely high noise — approximately 95% of lines carry no actionable information (heartbeats, partial content deltas, system bookkeeping)
- Each agent CLI has a different JSONL schema — no standard format exists across vendors
- New agent CLIs appear regularly (Gemini CLI, Copilot CLI launched recently), each requiring new parsing logic
- Stream processing performance matters: filtering must be fast because it runs on every line from every agent

## Decision Drivers

| Driver | Priority | Why It Matters |
|---|---|---|
| Normalize heterogeneous JSONL formats into unified events | Critical | Downstream components (Monitor, UICallback, StallDetector) must not care which agent produced the event |
| High-throughput filtering (95% noise reduction) | Critical | JSON parsing is expensive; filtering first avoids parsing ~95% of lines |
| Independent testability per stage | High | Each stage has distinct logic; testing a monolithic function is brittle |
| Extensibility for new agent formats | High | New CLIs appear regularly; adding a parser should not touch filter, monitor, or stall logic |
| Composability (use stages independently) | Medium | Some consuming projects may want filtering without monitoring, or monitoring without stall detection |
| State machine for lifecycle tracking | Medium | Precise agent state tracking prevents impossible states and simplifies debugging |

## Considered Options

### Option 1: Four-stage composable pipeline (Filter, Parse, Monitor, Stall Detect) (CHOSEN)

**Overview:** Decompose stream processing into four independent, chainable stages. Each stage has a single responsibility, well-defined input/output types, and can be used, tested, or replaced independently.

```
Raw JSONL → StreamFilter → StreamParser → AgentMonitor → StallDetector
              (string)      (StreamEvent)   (AgentState)    (timeout)
```

**Stage Details:**
1. **StreamFilter**: Takes a raw string line, returns bool (pass/drop). Uses string matching (substring and prefix checks) configured per StreamFormat. No JSON parsing. Approximately 95% drop rate.
2. **StreamParser**: Takes a filtered string line and StreamFormat, returns StreamEvent or None. Contains 5 format-specific parser functions. Produces normalized StreamEvent with unified field names.
3. **AgentMonitor**: Takes StreamEvent, updates internal state machine (9 states). Tracks tool executions. Provides snapshot() for point-in-time state.
4. **StallDetector**: Receives activity pings from events. Runs as concurrent asyncio task. Injects synthetic STALL events when timeout exceeded.

**Pros:**
- Single responsibility per stage: filter knows nothing about parsing; parser knows nothing about state tracking
- Each stage is independently testable with simple fixtures
- New agent format support requires only a new parser function in Stage 2 — Stages 1, 3, and 4 are unaffected
- Performance: filtering before parsing avoids JSON parsing on ~95% of lines
- Composable: a consuming project can use StreamFilter alone for log reduction, or StreamParser alone for analytics
- The state machine in AgentMonitor makes agent lifecycle explicit and prevents impossible states
- StallDetector as a separate stage means stall timeout logic is not tangled with parsing or state tracking

**Cons:**
- Four stages add architectural complexity compared to a single function
- Data flows through multiple objects per line — minor memory overhead for intermediate representations
- The StreamEvent model must accommodate all agent formats, leading to optional fields

**Risk Assessment:**

| Risk Type | Level | Detail |
|---|---|---|
| Technical | Low | Each stage is simpler than a monolithic processor; complexity is managed through decomposition |
| Schedule | Low | Planora already has filter and parse stages; extending to 4 stages is incremental |
| Ecosystem | Low | No third-party dependencies in the pipeline; pure Python |

**Trade-offs:**
- We gain independent testability, extensibility for new agents, and performance (filter before parse), accepting slightly more architectural complexity and optional fields in StreamEvent

---

### Option 2: Monolithic stream processor

**Overview:** A single class that takes raw JSONL lines and produces StreamEvent instances, handling filtering, parsing, state tracking, and stall detection internally.

**Pros:**
- Simpler architecture — one class, one call
- No intermediate data representations
- All logic in one place

**Cons:**
- Cannot test filtering without testing parsing
- Adding a new agent format requires modifying the monolithic class, risking regressions in existing parsers
- Stall detection tangled with parsing — timeout logic mixed with JSONL parsing
- Cannot use individual stages independently (e.g., filter alone for log reduction)
- Single Responsibility Principle violation — one class doing four things

**Why not chosen:**
- The monolithic approach is exactly what the four source systems already do, and it's the primary cause of the maintenance problems orchcore is designed to solve. Each source system has a monolithic stream handler that mixes filtering, parsing, state tracking, and timeout logic, making it impossible to extend or test individual concerns.

---

### Option 3: Plugin-based parser registry

**Overview:** Each agent format is a plugin that registers itself with a parser registry. The pipeline auto-discovers parsers at runtime.

**Pros:**
- Maximum extensibility — third parties can contribute parsers
- Clean separation between parser implementations
- No modification to core code when adding a new parser

**Cons:**
- Plugin discovery and registration adds complexity (entry points, importlib)
- Overkill for 5 known formats — plugins shine at scale (50+ formats), not at 5
- Harder to type-check (plugins are loaded dynamically)
- Testing requires mock plugin registration

**Why not chosen:**
- With 5 known agent formats and a single developer, plugin infrastructure is premature. The four-stage pipeline with a format-specific parser function per agent provides sufficient extensibility without the complexity of plugin discovery. If the number of formats grows beyond 10-15, revisiting this with a plugin approach would be worthwhile.

## Decision

**We have decided to implement stream processing as a four-stage composable pipeline: StreamFilter (noise reduction via string matching), StreamParser (format-specific JSONL normalization to StreamEvent), AgentMonitor (event-driven state machine), and StallDetector (activity-aware watchdog).**

### Implementation Details

- StreamFilter uses a `dict[StreamFormat, list[str]]` of skip patterns. A line is dropped if it contains any skip pattern for the current format. No regex — pure `in` string matching for speed.
- StreamParser dispatches to `_parse_claude()`, `_parse_codex()`, `_parse_opencode()`, `_parse_gemini()`, or `_parse_copilot()` based on the StreamFormat enum. Each branch returns `list[StreamEvent]`, allowing a single line to emit zero, one, or multiple events.
- AgentMonitor maintains an `AgentState` enum and a list of `ToolExecution` instances. State transitions are explicit: `_TRANSITIONS: dict[tuple[AgentState, StreamEventType], AgentState]`.
- StallDetector exposes a `watch(events: AsyncIterator[StreamEvent]) -> AsyncIterator[StreamEvent]` method that wraps the event stream. It uses `asyncio.wait_for()` with a `check_interval` to poll for new events, yielding a synthetic `STALL` event when idle time exceeds the configured timeout. Deep tool patterns (Exa, Tavily) trigger the higher `deep_timeout`.
- All four stages are instantiated per agent — no shared state across agents.

### When to Revisit This Decision

- If the number of supported agent formats exceeds 15 (consider plugin registry)
- If stream processing becomes a CPU bottleneck (consider Cython/Rust acceleration for the filter)
- If consuming projects need custom filter rules (consider making filter patterns configurable via TOML, not just per-format defaults)
- If agents start producing non-JSONL output (binary, protobuf) requiring a different parsing approach

## Consequences

### Positive

- Each stage is independently testable with simple string/dict fixtures
- New agent format support requires only a new `_parse_X()` function — no changes to filter, monitor, or stall logic
- Performance: ~95% of raw lines are dropped by string matching before any JSON parsing occurs
- Stages are composable: consuming projects can use StreamFilter alone for log reduction
- AgentMonitor's explicit state machine prevents impossible states and provides clear lifecycle tracking
- StallDetector's separation from the parser means stall logic can be disabled or customized without affecting parsing

### Negative

- Four classes instead of one adds architectural surface area
- StreamEvent has optional fields to accommodate all formats — some fields are None for some agents
- Per-agent instantiation means N agents create 4N stage objects (negligible memory impact in practice)

### Neutral

- The pipeline pattern is well-understood in software engineering — no learning curve
- Moving from monolithic to pipeline is a common refactoring pattern validated in production systems

## Validation and Monitoring

| Success Metric | Target | How to Measure |
|---|---|---|
| StreamFilter drop rate | ~95% for Claude, ~90% for others | Count filtered vs. total lines in test fixtures |
| StreamParser parse success rate | > 99% for non-filtered lines | Count successful parses vs. parse failures in test fixtures |
| AgentMonitor state transitions | Zero impossible state transitions | Exhaustive state machine test with all StreamEventType inputs |
| StallDetector accuracy | Fires within 1 second of timeout | Async test with controlled event timing |
| New agent format addition time | < 2 hours (parser function + test fixtures) | Time the addition of Gemini or Copilot parser |

**Review Schedule:**
- On each new agent CLI release: verify filter patterns and parser still work
- Quarterly: Review stream processing performance metrics
- Annually: Reassess pipeline architecture vs. alternatives

## Related Decisions

- **ADR-001:** [Extract reusable orchestration core](./001-extract-reusable-orchestration-core.md) — stream pipeline is a core component
- **ADR-002:** [Async-first architecture](./002-async-first-architecture-with-asyncio.md) — StallDetector uses asyncio tasks
- **ADR-003:** [Protocol-based UI decoupling](./003-protocol-based-ui-decoupling.md) — pipeline emits events via UICallback
- **ADR-007:** [Registry pattern for agents](./007-registry-pattern-for-agent-management.md) — StreamFormat comes from AgentConfig in registry

## References

- [Pipeline pattern](https://en.wikipedia.org/wiki/Pipeline_(software))
- [State Machine pattern](https://refactoring.guru/design-patterns/state)
- Claude Code JSONL format (internal documentation)
- Codex CLI JSONL format (internal documentation)

## Document History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-03-25 | Abdelaziz Abdelrasol | Initial version (ACCEPTED) |
