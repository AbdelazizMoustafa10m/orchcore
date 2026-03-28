---
id: ADR-003
title: Use Protocol-based UI decoupling via UICallback
status: ACCEPTED
date: 2026-03-25
decision_makers: [Abdelaziz Abdelrasol]
consulted: []
informed: []
confidence: HIGH
tags: [ui, protocol, decoupling, observer, callback]
related_decisions: [ADR-001, ADR-006]
supersedes: []
superseded_by: []
---

# ADR-003: Use Protocol-based UI decoupling via UICallback

## Status

ACCEPTED

## Context and Problem Statement

orchcore must support multiple, fundamentally different presentation layers across its consuming projects. Planora uses a Textual-based TUI with live-updating panels, progress bars, and interactive elements. Articles and Finvault use simple Bash echo statements with color codes. Raven uses Bash output with structured status lines. Future consumers might use Rich for CLI output, a web dashboard, or headless JSONL output for machine consumption.

The orchestration engine (pipeline execution, stream processing, rate-limit recovery) needs to communicate progress, events, errors, and state changes to the presentation layer without knowing what that layer is. If orchcore imports Rich, it forces all consumers to install Rich. If it imports Textual, it couples the engine to a TUI framework. If it uses bare print statements, consumers cannot customize the output.

In the four source systems, this decoupling is handled poorly. Planora has a custom callback system but it's not formalized as a protocol. The Bash systems use direct echo/printf calls, making it impossible to redirect output without modifying the orchestration logic. Rate-limit recovery messages are interleaved with stream processing output with no structured separation.

### Business Context

- Planora (TUI with Textual), Articles/Finvault/Raven (CLI with Bash echo), and future projects (web dashboard, headless CI) need the same orchestration engine with different UIs
- orchcore must not have runtime dependencies on any UI framework
- The event model must be rich enough to support TUI live-updating without polling
- Consuming projects need to opt into only the events they care about

## Decision Drivers

| Driver | Priority | Why It Matters |
|---|---|---|
| Support N presentation layers without N engine implementations | Critical | Each consuming project has a different UI; duplicating the engine per UI is what we're trying to eliminate |
| Zero runtime dependency on UI frameworks | Critical | orchcore must work without Rich, Textual, curses, or any display library installed |
| Type-safe event interface | High | Consuming projects need IDE autocompletion and mypy checking for callback methods |
| Opt-in event handling | High | A simple CLI doesn't need all 14+ event types; unused callbacks should be no-ops |
| Event-driven (not polling) | Medium | TUI live-updating requires push-based events, not periodic state checks |
| Low coupling between engine and presentation | Medium | Engine changes should not require presentation changes and vice versa |

## Considered Options

### Option 1: Python Protocol with typed callback methods (CHOSEN)

**Overview:** Define a `UICallback` Protocol (PEP 544) with 14+ typed methods covering pipeline, phase, agent, recovery, and system lifecycle events. orchcore calls these methods at appropriate points. Consuming projects implement the Protocol with their UI logic. orchcore provides `NullCallback` (no-op) and `LoggingCallback` (file logging) as reference implementations.

**Pros:**
- Structural subtyping: consuming projects don't need to inherit from or import orchcore's base class
- Full type safety: mypy checks that implementations have correct method signatures
- IDE support: autocompletion shows all available callback methods and their parameter types
- No runtime dependency on any UI framework
- Clear, discoverable interface: reading the Protocol definition shows all events the engine can emit
- Default implementations (NullCallback) allow consumers to implement only the callbacks they need
- Each method has typed parameters (StreamEvent, PhaseResult, etc.) providing rich data to the UI

**Cons:**
- Protocol requires implementing all methods (or using a base class with no-op defaults)
- Adding a new callback method is technically a breaking change (existing implementations would be missing the new method)
- 14+ methods is a relatively large interface — could be seen as violating Interface Segregation Principle

**Risk Assessment:**

| Risk Type | Level | Detail |
|---|---|---|
| Technical | Low | Python Protocols are well-established since Python 3.8 |
| Schedule | Low | UICallback Protocol is straightforward to define; Planora already has a similar pattern |
| Ecosystem | Low | Protocols are standard Python; no third-party dependency |

**Trade-offs:**
- We gain type-safe, zero-dependency UI decoupling with IDE support, accepting a relatively large interface and the need for default implementations to handle partial adoption

---

### Option 2: Abstract Base Class (ABC) with inheritance

**Overview:** Define UICallback as an ABC with abstract methods. Consuming projects inherit from it and override methods.

**Pros:**
- `isinstance()` checks work at runtime
- Can provide default (no-op) implementations via concrete methods
- Familiar to Java/C# developers

**Cons:**
- Requires consuming projects to `import` and `inherit from` orchcore's class — tighter coupling
- No structural subtyping: a class with all the right methods but no inheritance fails type checks
- Encourages deep inheritance hierarchies (UICallbackBase -> CLICallback -> RichCLICallback -> ...)
- ABC mixin behavior can create complex MRO (Method Resolution Order) issues

**Why not chosen:**
- Protocol provides the same type safety without requiring inheritance. Consuming projects can implement UICallback without importing orchcore's base class, which is the level of decoupling we need. A Bash-to-Python migration can implement the interface without depending on orchcore at import time.

---

### Option 3: Event bus (publish-subscribe)

**Overview:** Use a publish-subscribe event bus (e.g., `blinker` library or custom EventEmitter) where orchcore publishes typed events and consuming projects subscribe handlers.

**Pros:**
- Multiple subscribers per event (useful for logging + UI simultaneously)
- Subscribers don't need to implement all events — subscribe only to what you need
- New events can be added without breaking existing subscribers
- Decoupled registration: subscribers register at runtime, not at type level

**Cons:**
- Loses type safety: event payloads are typically `dict` or generic types
- Harder to discover available events (no single interface to read)
- Debugging is harder: events flow through an indirection layer with no direct call stack
- Adds a dependency (blinker) or requires implementing a custom event system
- Order of subscriber execution is often undefined

**Why not chosen:**
- The loss of type safety is unacceptable for a library that uses mypy strict mode. UICallback's explicit Protocol provides better discoverability (read the Protocol, see all events), better type checking (mypy validates parameter types), and simpler debugging (direct method calls have clear stack traces).

---

### Option 4: Direct logging (structured log events)

**Overview:** orchcore emits structured log events (JSON to stderr or a log file) and consuming projects parse the log stream for UI updates.

**Pros:**
- No interface to implement — consumers just read logs
- Works across process boundaries (Bash scripts can grep log output)
- Logging is already needed for file-based audit trails

**Cons:**
- Log parsing is fragile: format changes break consumers
- No type safety on the consumer side
- High latency for TUI updates (parse JSON per line)
- Mixing UI events with operational logs creates noise
- Cannot support interactive UI patterns (progress bars, live updating panels)

**Why not chosen:**
- Structured logging is excellent for audit trails and file records but insufficient for interactive UIs. Planora's TUI needs sub-second event delivery with typed data — log parsing cannot provide this. orchcore uses UICallback for real-time UI and file logging for audit trails, keeping the two concerns separate.

## Decision

**We have decided to use a Python Protocol (`UICallback`) with 14+ typed callback methods as the sole mechanism for decoupling orchcore's orchestration engine from presentation layers.**

### Implementation Details

- `UICallback` is defined as a `typing.Protocol` with runtime-checkable support (`@runtime_checkable`)
- Methods cover 5 lifecycle scopes: pipeline (start/complete), phase (start/end/skip), agent (start/event/complete/error), recovery (stall/rate_limit/retry/git_recovery), and system (shutdown)
- `NullCallback` implements all methods as no-ops — used as the default when no UI is needed
- `LoggingCallback` implements all methods by writing structured entries to a log file — used for headless/CI execution
- Consuming projects implement the Protocol (or subclass NullCallback for partial implementation)
- All callback methods receive typed Pydantic models (StreamEvent, PhaseResult, AgentResult) as parameters
- Callbacks are synchronous (not async) to keep UI code simple; async-to-sync bridging is orchcore's responsibility

### When to Revisit This Decision

- If the number of callback methods exceeds 25 (consider splitting into sub-protocols: PipelineCallback, AgentCallback, RecoveryCallback)
- If consuming projects need async callbacks (e.g., for async TUI frameworks)
- If a consuming project needs multiple simultaneous UI implementations (consider adding CompositeCallback that delegates to multiple implementations)
- If the Protocol approach causes friction for Bash-based consumers (consider a JSONL output adapter)

## Consequences

### Positive

- orchcore has zero runtime dependency on any UI framework (Rich, Textual, curses, etc.)
- Consuming projects get full type safety and IDE autocompletion for callback methods
- The same orchestration engine code serves CLI, TUI, web, and headless consumers
- Adding a new consuming project requires only implementing UICallback — no engine changes
- NullCallback enables using orchcore in tests and scripts without any UI setup
- LoggingCallback provides a built-in audit trail for headless/CI execution

### Negative

- 14+ methods is a moderately large interface; implementing all of them is tedious for simple consumers (mitigated by NullCallback as a base class)
- Adding a new callback method requires updating NullCallback and LoggingCallback, and is technically a breaking change for consumers that implement the Protocol directly (mitigated by semver and deprecation notices)
- Callbacks are synchronous, which may limit performance for I/O-heavy UI operations (mitigated by keeping callbacks lightweight — heavy work should be queued)

### Neutral

- The Protocol pattern is standard Python — no learning curve for experienced Python developers
- The callback method naming convention (`on_<scope>_<event>`) is self-documenting

## Validation and Monitoring

| Success Metric | Target | How to Measure |
|---|---|---|
| Consuming projects can implement UICallback without importing orchcore internals | Protocol structural subtyping works | Verify with mypy on a consumer that doesn't import orchcore.ui |
| NullCallback has zero overhead | < 1 microsecond per callback invocation | Benchmark test |
| Planora TUI successfully implements UICallback | All 14+ methods mapped to Textual widgets | Integration test with Planora |
| LoggingCallback produces parseable audit trail | JSON log entries for all event types | Unit test validating log format |

**Review Schedule:**
- Quarterly: Review callback usage patterns across consumers; identify unused or missing methods
- Annually: Reassess Protocol vs. alternatives

## Related Decisions

- **ADR-001:** [Extract reusable orchestration core](./001-extract-reusable-orchestration-core.md) — UICallback is the primary decoupling mechanism
- **ADR-006:** [Use Pydantic for all data models](./006-pydantic-for-all-data-models.md) — callback parameters are Pydantic models

## References

- [PEP 544 — Protocols: Structural subtyping](https://peps.python.org/pep-0544/)
- [Python typing — Protocol](https://docs.python.org/3/library/typing.html#typing.Protocol)
- [Observer Pattern (GoF)](https://refactoring.guru/design-patterns/observer)

## Document History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-03-25 | Abdelaziz Abdelrasol | Initial version (ACCEPTED) |
