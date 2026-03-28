---
id: ADR-001
title: Extract reusable orchestration core as standalone Python package
status: ACCEPTED
date: 2026-03-25
decision_makers: [Abdelaziz Abdelrasol]
consulted: []
informed: []
confidence: HIGH
tags: [architecture, packaging, extraction, reuse]
related_decisions: [ADR-002, ADR-003, ADR-004, ADR-005, ADR-006, ADR-007, ADR-008]
supersedes: []
superseded_by: []
---

# ADR-001: Extract reusable orchestration core as standalone Python package

## Status

ACCEPTED

## Context and Problem Statement

Over the course of building four production AI agent orchestration systems — Planora (Python CLI/TUI for multi-agent implementation planning), Articles (Bash 8-phase article writing pipeline), Finvault (Bash multi-agent performance audit and code review), and Raven/Ralph (Bash autonomous task-driven development with loop recovery) — a clear pattern emerged: approximately 60-70% of the orchestration code across all four systems is functionally identical infrastructure.

Each system independently implements subprocess launching for agent CLIs, JSONL stream parsing, rate-limit detection and recovery, workspace management, configuration handling, and signal-based shutdown. Bug fixes discovered in one system (such as a new rate-limit detection pattern for Claude CLI, or a timezone parsing fix for reset times) must be manually ported to the other three systems. New agent CLI support (e.g., when Gemini CLI launched) requires implementing a stream parser in each system separately.

The AI agent CLI ecosystem is expanding rapidly. Each new agent CLI means N implementations across N systems. Without a shared library, the maintenance burden scales linearly with both the number of orchestration systems and the number of supported agents.

### Business Context

- Four production systems actively maintained by a single developer, creating unsustainable duplication
- The Python ecosystem lacks a purpose-built library for orchestrating CLI-based AI coding agents (existing tools like LangChain and CrewAI operate at the API level, not the subprocess level)
- New orchestration projects are planned, and each would re-derive the same infrastructure without a shared package
- The cost of a bug in rate-limit recovery or signal handling is multiplied by the number of systems

## Decision Drivers

| Driver | Priority | Why It Matters |
|---|---|---|
| Eliminate code duplication | Critical | 60-70% identical infrastructure across 4 systems; every bug fix must be ported 4 times |
| Reduce time to build new orchestration systems | Critical | New projects should define domain logic only, not reimplement subprocess management |
| Single source of truth for bug fixes | High | A timezone parsing fix should benefit all consumers immediately |
| Support growing agent CLI ecosystem | High | New agents (Gemini, Copilot) keep appearing; shared parsers are essential |
| Maintain consuming project autonomy | High | Each system has different entry points (Bash, Python CLI, TUI); must not force a single architecture |
| Type safety across the stack | Medium | Multi-agent orchestration has subtle failure modes that type checking catches |

## Considered Options

### Option 1: Extract into standalone Python package `orchcore` (CHOSEN)

**Overview:** Analyze all four source systems, identify the common orchestration patterns, and extract them into a pip-installable Python package with 10 components. Consuming projects import the components they need.

**Pros:**
- Eliminates duplication: common code exists in exactly one place
- Each consuming project keeps its domain logic untouched
- New orchestration projects start with a tested, documented foundation
- Bug fixes and improvements benefit all consumers immediately via version bumps
- Type-safe Python with mypy strict mode catches errors at development time
- Standard Python packaging (PyPI, pip) makes distribution trivial
- Each component is independently usable — no all-or-nothing dependency

**Cons:**
- Upfront investment in design, extraction, and testing (~12 weeks)
- Bash-based systems (Articles, Finvault, Raven) require migration effort or a shim layer
- Versioning discipline required — breaking changes affect all consumers
- Single developer must maintain the package alongside consuming projects

**Risk Assessment:**

| Risk Type | Level | Detail |
|---|---|---|
| Technical | Low | The patterns are well-proven across 4 production systems; extraction is refactoring, not invention |
| Schedule | Medium | 12-week estimate for v1.0; Planora migration adds 2-3 weeks |
| Ecosystem | Low | Python packaging is mature; pydantic and asyncio are stable foundations |

**Trade-offs:**
- We gain single source of truth and elimination of duplication, accepting upfront extraction effort and ongoing versioning discipline
- We gain type safety and testability, accepting that Bash-based consumers need migration or a shim

---

### Option 2: Shared Bash library (shell functions)

**Overview:** Extract common patterns into a Bash library of shared functions (a sourced shell script) that Articles, Finvault, and Raven can source.

**Pros:**
- Minimal migration for existing Bash systems
- No language barrier — stays in the existing ecosystem
- Simple distribution (single file or git submodule)

**Cons:**
- Bash lacks type safety, structured error handling, and async primitives
- Cannot support the complexity of stream parsing, state machines, or layered configuration
- Planora (Python) cannot use Bash functions — would still need a separate implementation
- Testing Bash functions is fragile and limited (no mocking, no type checking)
- Does not solve the fundamental problem for Python-based consumers

**Why not chosen:**
- Bash fundamentally lacks the language features (async, types, data models) needed for reliable orchestration infrastructure. It would perpetuate the quality gap between Planora's Python implementation and the Bash systems.

---

### Option 3: Monorepo with shared modules (no package extraction)

**Overview:** Move all four systems into a single monorepo with a `shared/` directory containing common modules. Each system imports from `shared/` via relative paths.

**Pros:**
- No packaging overhead — direct imports
- Atomic changes across shared code and consumers
- Simpler CI (one repo, one pipeline)

**Cons:**
- Couples all four systems' release cycles — a change in Planora could break Articles
- Relative imports are fragile and don't work well across Python/Bash boundaries
- No version pinning — consumers always get latest shared code (no stability guarantee)
- Monorepo tooling (Bazel, pants) adds complexity disproportionate to a single-developer project
- Does not support external consumers (future projects, open-source)

**Why not chosen:**
- The four systems have fundamentally different architectures (Python vs. Bash, CLI vs. TUI) and release cycles. Coupling them in a monorepo would create more problems than it solves. A properly versioned package provides stability boundaries.

## Decision

**We have decided to extract the common orchestration infrastructure from all four production systems into a standalone, pip-installable Python package called `orchcore`.**

### Implementation Details

- Package name: `orchcore`
- Python version: >= 3.12
- Build system: hatchling with `src/orchcore` layout
- Distribution: PyPI via standard `pip install orchcore`
- 10 components: registry, runner, stream, pipeline, recovery, workspace, config, prompt, display, signals, plus UICallback protocol
- Core dependencies: pydantic >= 2.10, pydantic-settings >= 2.7, jinja2 >= 3.1
- Optional dependencies: rich (CLI display), textual (TUI base), opentelemetry (tracing via `telemetry` extra)
- Type checking: mypy strict mode

### When to Revisit This Decision

- If the number of consuming projects drops to 1 (extraction overhead no longer justified)
- If a mature third-party library emerges for CLI-based AI agent orchestration
- If the Python ecosystem shifts away from asyncio toward a different concurrency model
- If maintaining backward compatibility across consumers becomes unsustainably expensive

## Consequences

### Positive

- Eliminates 60-70% code duplication across four production systems
- Bug fixes in orchcore benefit all consumers immediately via version bumps
- New orchestration projects start with tested, documented infrastructure
- Adding a new agent CLI requires a single TOML configuration entry, not N implementations
- Type safety (mypy strict) catches errors that Bash-based systems silently propagate
- Standard Python packaging enables clean dependency management and versioning

### Negative

- Upfront investment of ~12 weeks for design, extraction, testing, and initial migration
- Bash-based systems (Articles, Finvault, Raven) require migration to Python or a subprocess shim
- Versioning discipline adds overhead: semver, changelogs, deprecation notices
- Single developer becomes the bottleneck for orchcore maintenance

### Neutral

- Package structure (10 components) mirrors the natural module boundaries already present in Planora
- asyncio-first design aligns with Python 3.12+ best practices
- TOML configuration aligns with Python ecosystem conventions (pyproject.toml)

## Validation and Monitoring

| Success Metric | Target | How to Measure |
|---|---|---|
| Code duplication across systems | < 5% (down from 60-70%) | LOC comparison of pre/post migration |
| Time to add new agent support | < 30 minutes (down from 2-4 hours per system) | Time a real addition |
| Bug fix propagation | Single commit in orchcore (down from 4 manual ports) | Count of cross-system bug fixes over 6 months |
| Test coverage | > 90% for orchcore | pytest-cov report |

**Review Schedule:**
- Quarterly: Review consumer feedback, API stability, and test coverage
- Annually: Reassess extraction vs. alternatives (monorepo, third-party library)

## Related Decisions

- **ADR-002:** [Use async-first architecture with asyncio](./002-async-first-architecture-with-asyncio.md) — enabled by Python package extraction
- **ADR-003:** [Use Protocol-based UI decoupling](./003-protocol-based-ui-decoupling.md) — key design pattern within orchcore
- **ADR-004:** [Use composable stream processing pipeline](./004-composable-stream-processing-pipeline.md) — core component design
- **ADR-005:** [Use multi-source layered configuration](./005-multi-source-layered-configuration.md) — configuration system design
- **ADR-006:** [Use Pydantic for all data models](./006-pydantic-for-all-data-models.md) — type safety approach
- **ADR-007:** [Use registry pattern for agent management](./007-registry-pattern-for-agent-management.md) — extensibility approach
- **ADR-008:** [Use partial failure semantics with retry](./008-partial-failure-semantics-with-retry.md) — reliability approach

## References

- Planora source code (Python CLI/TUI, primary reference implementation)
- Articles source code (Bash 8-phase pipeline)
- Finvault source code (Bash multi-agent audit)
- Raven/Ralph source code (Bash autonomous development)
- [Python Packaging User Guide](https://packaging.python.org/)
- [hatchling documentation](https://hatch.pypa.io/)

## Document History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-03-25 | Abdelaziz Abdelrasol | Initial version (ACCEPTED) |
