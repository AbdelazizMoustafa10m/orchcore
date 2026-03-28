---
id: ADR-006
title: Use Pydantic for all data models with mypy strict mode
status: ACCEPTED
date: 2026-03-25
decision_makers: [Abdelaziz Abdelrasol]
consulted: []
informed: []
confidence: HIGH
tags: [type-safety, pydantic, mypy, data-models, validation]
related_decisions: [ADR-001, ADR-003, ADR-005]
supersedes: []
superseded_by: []
---

# ADR-006: Use Pydantic for all data models with mypy strict mode

## Status

ACCEPTED

## Context and Problem Statement

orchcore has approximately 20 data structures that cross component boundaries: AgentConfig, AgentResult, StreamEvent, Phase, PhaseResult, PipelineResult, ToolExecution, AgentMonitorSnapshot, RetryPolicy, OutputExtraction, and others. These models carry data between the registry and runner, between the stream pipeline and UICallback, between the phase engine and consuming projects, and between the recovery module and the runner.

In multi-agent orchestration, data integrity at component boundaries is critical. A misconfigured AgentConfig (wrong type for stall_timeout, missing binary path) causes a subprocess launch failure that manifests far from the misconfiguration. A malformed StreamEvent (wrong enum value, missing timestamp) causes the AgentMonitor state machine to enter an undefined state. A PhaseResult with an invalid status causes the PipelineRunner to make incorrect dependency decisions.

In the four source systems:
- **Planora** (Python): Uses Pydantic v2 for some models, but not consistently — some data flows use raw dicts
- **Articles/Finvault/Raven** (Bash): No type safety — all data is untyped strings passed between functions

The lack of consistent type safety across the source systems has been a recurring source of bugs: wrong field names in dicts, missing fields silently defaulting to None, numeric strings not converted to integers, and invalid enum values passing through unchecked.

### Business Context

- orchcore is critical infrastructure — bugs propagate to all consuming projects
- Multi-agent orchestration has subtle failure modes that are hard to debug without type safety
- The Python ecosystem has converged on Pydantic v2 as the standard for data validation
- mypy strict mode provides compile-time checking that catches type errors before runtime

## Decision Drivers

| Driver | Priority | Why It Matters |
|---|---|---|
| Runtime validation at component boundaries | Critical | Catches misconfiguration, malformed data, and type errors at the point of entry, not at the point of failure |
| Static type checking (mypy strict) | Critical | Catches type errors during development, before code runs in production |
| Serialization (to/from JSON, dict, TOML) | High | AgentResult, PhaseResult, and timing records need to be serialized for archival and resume |
| IDE autocompletion and documentation | High | Engineers working with orchcore need discoverability of model fields and types |
| Immutability for safety | Medium | Models like AgentConfig and Phase should not be mutated after creation |
| Compatibility with pydantic-settings | Medium | Configuration system (ADR-005) uses pydantic-settings, which requires Pydantic models |

## Considered Options

### Option 1: Pydantic BaseModel for all data structures with mypy strict (CHOSEN)

**Overview:** Every data structure that crosses a component boundary is a Pydantic BaseModel (or StrEnum). All fields have explicit type annotations. The entire package runs under mypy strict mode with zero errors. Validation happens at model construction time.

**Pros:**
- Runtime validation at construction: `AgentConfig(stall_timeout="abc")` raises `ValidationError` immediately
- Static type checking: mypy strict catches `result.exit_cod` (typo) at development time
- Automatic serialization: `.model_dump()`, `.model_dump_json()`, `.model_validate()` for JSON and dict conversion
- IDE integration: autocompletion, type hints, field documentation via `Field(description=...)`
- Immutability: `model_config = ConfigDict(frozen=True)` prevents accidental mutation
- Schema generation: `.model_json_schema()` produces JSON Schema for documentation
- pydantic-settings compatibility: configuration system (ADR-005) directly uses Pydantic's type system
- Ecosystem standard: most Python libraries understand and integrate with Pydantic models

**Cons:**
- Pydantic v2 is a required dependency (~2MB installed size)
- Construction overhead: Pydantic validation adds ~1-5 microseconds per model instance (negligible for orchcore's throughput)
- Learning curve: developers must understand Pydantic v2's validation semantics (validators, model_config, etc.)
- mypy strict mode is demanding: requires explicit return types, no implicit Any, no untyped function signatures

**Risk Assessment:**

| Risk Type | Level | Detail |
|---|---|---|
| Technical | Low | Pydantic v2 is stable and battle-tested |
| Schedule | Low | Planora already uses Pydantic — patterns are established |
| Ecosystem | Low | Pydantic is the most widely used Python data library; not at risk of abandonment |

**Trade-offs:**
- We gain runtime validation, static type checking, and serialization at every component boundary, accepting Pydantic as a dependency and the discipline of mypy strict mode

---

### Option 2: Python dataclasses (stdlib)

**Overview:** Use `@dataclass` for all data structures. Type hints provide documentation and mypy checking, but no runtime validation.

**Pros:**
- No additional dependency (stdlib)
- Lighter weight than Pydantic (~10x faster construction)
- Familiar to all Python developers
- mypy strict works with dataclasses

**Cons:**
- No runtime validation: `AgentConfig(stall_timeout="abc")` silently stores a string where int is expected
- No automatic serialization: must write custom `to_dict()`, `from_dict()` methods for each model
- No schema generation
- Less expressive validation: custom `__post_init__` is fragile and verbose
- Not compatible with pydantic-settings without wrapping

**Why not chosen:**
- The absence of runtime validation is unacceptable for a library that processes untrusted data from agent CLI output and user-provided configuration. A malformed StreamEvent from a parser bug would propagate silently through the system until it causes a failure far from the source. Pydantic catches this at construction time.

---

### Option 3: attrs

**Overview:** Use the attrs library for data classes with optional validation via `@attr.s(slots=True)` and validators.

**Pros:**
- Lighter weight than Pydantic
- Good performance (slotted classes)
- Validators available (opt-in)
- mypy plugin provides good type checking

**Cons:**
- Validation is less expressive than Pydantic (no complex types, no automatic coercion)
- No automatic JSON/dict serialization (need cattrs as companion)
- Smaller ecosystem integration (fewer libraries understand attrs natively)
- Not compatible with pydantic-settings
- Two data libraries (attrs + cattrs) instead of one (pydantic)

**Why not chosen:**
- attrs + cattrs provides a subset of Pydantic's functionality with more dependencies and less ecosystem integration. Since orchcore already needs pydantic-settings for configuration (ADR-005), using Pydantic for all models is the natural, consistent choice.

---

### Option 4: TypedDict (stdlib)

**Overview:** Use TypedDict for structural typing of dictionaries. This provides type hints without requiring class instances.

**Pros:**
- No dependency (stdlib)
- Zero overhead (plain dicts)
- mypy checks field access on TypedDict

**Cons:**
- No runtime validation (TypedDict is purely a static analysis construct)
- Mutable by default (no frozen option)
- No methods (no `.model_dump()`, no `.snapshot()`)
- No inheritance with proper field merging
- Awkward syntax for optional fields and defaults
- Not compatible with pydantic-settings

**Why not chosen:**
- TypedDict provides static type hints but no runtime validation, no serialization, and no behavior (methods). orchcore models need runtime validation (parsing agent output), serialization (archiving results), and behavior (AgentMonitor.snapshot(), AgentRegistry.get()). TypedDict is insufficient for these requirements.

## Decision

**We have decided to use Pydantic BaseModel (>= 2.10) for all data structures that cross component boundaries, with mypy strict mode enforced across the entire orchcore package.**

### Implementation Details

- All models use `pydantic.BaseModel` with explicit type annotations for all fields
- Enums use `StrEnum` for string-based enumerations (AgentState, StreamEventType, PhaseStatus, StreamFormat, AgentMode)
- Immutable models use `model_config = ConfigDict(frozen=True)` for models that should not be mutated after creation (AgentConfig, Phase, StreamEvent)
- Mutable models (AgentMonitor internals, WorkspaceManager state) use regular BaseModel
- mypy configuration in `pyproject.toml`:
  ```toml
  [tool.mypy]
  strict = true
  plugins = ["pydantic.mypy"]

  [tool.pydantic-mypy]
  init_forbid_extra = true
  init_typed = true
  warn_required_dynamic_aliases = true
  ```
- Validation errors produce clear messages referencing the field name, expected type, and received value
- Serialization uses `.model_dump()` for dict output and `.model_dump_json()` for JSON output
- Deserialization uses `.model_validate()` for dict input and `.model_validate_json()` for JSON input

### When to Revisit This Decision

- If Pydantic v3 introduces breaking changes that require significant migration effort
- If a new Python stdlib mechanism provides runtime validation without external dependencies
- If construction overhead becomes measurable (> 1ms per model in profiling) — extremely unlikely given orchcore's throughput
- If mypy strict mode causes excessive friction for contributors (no sign of this with a single-developer project)

## Consequences

### Positive

- Runtime validation catches misconfiguration and malformed data at construction time, not at the point of failure
- mypy strict mode catches type errors during development with zero runtime cost
- Automatic serialization enables archival, resume, and debugging without custom code
- IDE autocompletion makes orchcore's data model discoverable
- Frozen models prevent accidental mutation of AgentConfig, Phase, and StreamEvent
- Consistent model pattern across all 20+ data structures reduces cognitive load
- pydantic-settings integration provides type-safe configuration with the same validation system

### Negative

- Pydantic v2 is a required dependency (mitigated: most Python projects already use Pydantic)
- mypy strict mode requires explicit type annotations everywhere (mitigated: this is a feature, not a bug — it forces clear API design)
- Minor construction overhead (mitigated: ~1-5 microseconds per instance, negligible vs. subprocess execution time)

### Neutral

- Pydantic is the de facto standard for Python data models — using it aligns with community expectations
- mypy strict mode is increasingly common in production Python packages

## Validation and Monitoring

| Success Metric | Target | How to Measure |
|---|---|---|
| mypy strict compliance | Zero errors on every commit | CI check: `mypy --strict src/orchcore` |
| Runtime validation coverage | All cross-boundary data structures are Pydantic models | Code review: no raw dicts crossing module boundaries |
| Serialization round-trip | All models survive `.model_dump()` / `.model_validate()` round-trip | Unit test for each model |
| Validation error clarity | Error messages include field name, expected type, and received value | Manual review of ValidationError output |

**Review Schedule:**
- On each pydantic release: check for breaking changes or deprecations
- Annually: Reassess Pydantic vs. alternatives

## Related Decisions

- **ADR-001:** [Extract reusable orchestration core](./001-extract-reusable-orchestration-core.md) — type safety is a key motivation for the Python package
- **ADR-003:** [Protocol-based UI decoupling](./003-protocol-based-ui-decoupling.md) — UICallback parameters are Pydantic models
- **ADR-005:** [Multi-source layered configuration](./005-multi-source-layered-configuration.md) — uses pydantic-settings (Pydantic ecosystem)

## References

- [Pydantic v2 documentation](https://docs.pydantic.dev/latest/)
- [Pydantic mypy plugin](https://docs.pydantic.dev/latest/integrations/mypy/)
- [mypy strict mode](https://mypy.readthedocs.io/en/stable/command_line.html#cmdoption-mypy-strict)
- [PEP 557 — Data Classes](https://peps.python.org/pep-0557/) (rejected alternative)

## Document History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-03-25 | Abdelaziz Abdelrasol | Initial version (ACCEPTED) |
