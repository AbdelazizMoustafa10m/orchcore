---
id: ADR-007
title: Use registry pattern for agent management
status: ACCEPTED
date: 2026-03-25
decision_makers:
  - Abdelaziz Abdelrasol
consulted: []
informed: []
confidence: HIGH
tags:
  - registry
  - agents
  - extensibility
  - configuration
  - toml
related_decisions:
  - ADR-001
  - ADR-004
  - ADR-005
  - ADR-006
  - ADR-009
  - ADR-011
supersedes: []
superseded_by: []
---

# ADR-007: Use registry pattern for agent management

## Status

ACCEPTED — amended by [ADR-011](./011-consumer-defined-flag-profiles.md) (2026-07-02): the `AgentMode` enum referenced below was replaced by consumer-defined flag profile names (`AgentConfig.flags: dict[str, ...]`). Historical mentions of `AgentMode`/`flags[mode]` in this record are preserved as written.

## Context and Problem Statement

orchcore must manage configurations for multiple AI coding agent CLIs — Claude Code, Codex, Gemini, Copilot, OpenCode — and support adding new agents as the ecosystem evolves. Each agent has a distinct binary name, model identifier, subcommand structure, mode-specific CLI flags, JSONL stream format, output extraction strategy, environment variable requirements, and timeout settings.

The fundamental question is: how should orchcore represent and manage these per-agent configurations? The implementation handles three scenarios:
1. Agents registered programmatically by consuming projects
2. Complete agent entries loaded from TOML files
3. Runtime per-agent field patches applied with `AgentRegistry.with_overrides()`

In the four source systems:
- **Planora** (Python): Agent configs are Python dicts defined inline in the orchestration code. Adding a new agent means editing Python source.
- **Articles** (Bash): Agent binary and flags are hardcoded in the script. No abstraction for multiple agents.
- **Finvault** (Bash): Agent binary and flags hardcoded per function. Switching agents means editing multiple functions.
- **Raven** (Bash): Agent binary is a variable, but flags, stream format, and output extraction are hardcoded for Claude only.

Every source system treats agent configuration as code rather than data. This means adding a new agent requires code changes, which is unsustainable as the agent CLI ecosystem grows.

### Business Context

- The AI agent CLI ecosystem is expanding: Gemini CLI and Copilot CLI launched within months of each other
- Users may want to use agent CLIs that orchcore doesn't know about (custom/internal tools)
- Different projects need different default models for the same agent (e.g., Claude with claude-sonnet-4-20250514 for code review, Claude with opus for complex planning)
- Configuration-only extensibility eliminates the need for orchcore releases when new agents appear

## Decision Drivers

| Driver | Priority | Why It Matters |
|---|---|---|
| Add new agent support without code changes | Critical | Agent CLI ecosystem is growing; orchcore cannot require a release for each new agent |
| Override per-agent fields at runtime | High | Users need to change models, timeouts, env handling, and flags without editing orchcore source |
| Central lookup by name | High | Runner, Pipeline, and Stream components need to resolve agent configs by name |
| Type-safe agent configuration | High | Invalid configs (wrong binary, missing flags) should fail fast with clear errors |
| No hardcoded agent policy in core | High | Consuming projects own the exact CLIs, models, flags, and auth posture they run |
| TOML-based configuration | Medium | Aligns with orchcore's configuration system (ADR-005) |

## Considered Options

### Option 1: Registry pattern with explicit registrations and TOML data (CHOSEN)

**Overview:** Implement an `AgentRegistry` that stores `AgentConfig` instances registered by consuming projects or loaded as complete entries from TOML files. The registry provides `get()`, `register()`, `list_agents()`, `available()`, `validate()`, `load_from_toml()`, and `with_overrides()` methods.

**Programmatic registration:**
```python
registry = AgentRegistry()
registry.register(
    AgentConfig(
        name="claude",
        binary="claude",
        model="claude-sonnet-4-20250514",
        subcommand="-p",
        flags={AgentMode.PLAN: ("--verbose",)},
        stream_format=StreamFormat.CLAUDE,
        output_extraction=OutputExtraction(strategy="jq_filter", jq_expression="..."),
    )
)
```

**TOML entries:**
```toml
[agents.claude]
binary = "claude"
model = "claude-opus-4-20250514"
subcommand = "-p"
stream_format = "claude"

[agents.claude.flags]
plan = ["--verbose"]

[agents.claude.output_extraction]
strategy = "jq_filter"
jq_expression = ".content[0].text"
```

**Pros:**
- Core has no stale hardcoded model/flag defaults to maintain
- Complete TOML entries allow per-project customization without code changes
- New agents can be added via TOML alone when they use a supported stream format
- Central `get(name)` lookup used by Runner, Pipeline, and Stream
- `AgentConfig` is a Pydantic model — type-safe with validation (ADR-006)
- `list_agents()` exposes configured agents; `available()` reports which registered agent binaries are on `PATH`
- `validate()` reports unknown or unavailable agent names before a pipeline run
- `register()` enables programmatic registration for testing and dynamic workflows
- `with_overrides()` returns a patched registry without mutating the original registry

**Cons:**
- Consumers must provide at least one registration or TOML file before pipeline execution
- New stream formats still require a parser implementation (TOML cannot add parsing logic)
- Registry is a global-ish singleton per orchestration session — must be thread-safe (asyncio single-thread makes this moot)

**Risk Assessment:**

| Risk Type | Level | Detail |
|---|---|---|
| Technical | Low | Registry pattern is well-established; AgentConfig is a straightforward Pydantic model |
| Schedule | Low | Registry storage, TOML parsing, and Pydantic validation are straightforward to implement |
| Ecosystem | Low | TOML parsing uses tomllib (stdlib); no third-party risk |

**Trade-offs:**
- We keep agent configuration as data and avoid stale baked-in defaults, accepting that consumers must provide registry entries before use and that new stream formats still require a parser implementation in Python

---

### Option 2: Class hierarchy per agent

**Overview:** Each agent has its own class inheriting from `BaseAgent`: `class ClaudeAgent(BaseAgent)`, `class CodexAgent(BaseAgent)`, etc. Classes encapsulate configuration, command building, and parsing.

**Pros:**
- Each agent's logic is fully encapsulated
- IDE navigation works (go to class definition)
- Can override behavior per agent (not just data)

**Cons:**
- Adding a new agent requires a new Python class — cannot be done via TOML
- Class proliferation: N agents = N classes + a factory/registry anyway
- Conflates configuration (data) with behavior (code) — an agent's binary path and timeout are data, not behavior
- Inheritance hierarchies tend to become rigid and hard to refactor
- orchcore must release a new version for each new agent class

**Why not chosen:**
- Agent configuration is fundamentally data, not behavior. The binary path, model name, CLI flags, stream format, and timeout are all data fields that differ per agent. Encoding them as class hierarchies conflates data with behavior and prevents TOML-only extensibility. The registry pattern treats agents as data and only uses code for behavior that truly varies (stream parsers).

---

### Option 3: Discovery-based plugin system

**Overview:** Agent configurations are distributed as Python entry points. orchcore discovers installed agent plugins at startup using `importlib.metadata`.

**Pros:**
- Maximum extensibility — third parties ship agent support as pip packages
- Clean separation between orchcore core and agent-specific code
- Follows Python packaging conventions (entry points)

**Cons:**
- Overkill for 5 known agents with a single developer
- Plugin discovery adds startup latency
- Harder to debug (which plugin is loaded? which version?)
- Users must `pip install orchcore-agent-gemini` instead of editing a TOML file
- Plugin API stability is hard to maintain

**Why not chosen:**
- Plugin systems are valuable when there are many (10+) independent contributors developing extensions. With 5 known agents and a single developer, TOML-based configuration provides equivalent extensibility with dramatically less complexity. If the number of agents grows beyond 15 or community contributions become common, revisiting this with a plugin approach would be worthwhile.

## Decision

**We have decided to use a registry pattern where agent configurations are data owned by the consuming project. `AgentRegistry` has no hardcoded built-ins; consumers register agents programmatically or load complete entries from TOML files. Runtime patches are applied with `with_overrides()`.**

### Implementation Details

- `AgentRegistry.__init__(agents: dict[str, AgentConfig] | None = None)`: starts empty unless an initial mapping is supplied.
- `AgentRegistry.get(name: str) -> AgentConfig`: returns the registered config. Raises `KeyError` if unknown.
- `AgentRegistry.register(config: AgentConfig) -> None`: adds or replaces an agent config programmatically
- `AgentRegistry.list_agents() -> list[str]`: returns registered agent names
- `AgentRegistry.available() -> list[str]`: returns registered agents whose binary resolves on `PATH`
- `AgentRegistry.validate(names: list[str]) -> list[str]`: returns names that are unknown or whose binary is unavailable
- `AgentRegistry.load_from_toml(path: Path, *, on_error: Literal["raise", "skip"] = "raise") -> None`: loads complete `[agents.<name>]` entries. With the default `on_error="raise"`, loading is atomic: all entries are parsed and validated before any registration occurs, and a single validation failure leaves the registry unchanged. `on_error="skip"` registers valid entries and logs invalid ones.
- `AgentRegistry.with_overrides(overrides: Mapping[str, AgentOverrideConfig | dict[str, Any]]) -> AgentRegistry`: returns a new registry with field patches applied to matching registered agents. This is the override mechanism; TOML loading does not merge partial entries into implicit defaults.

### Relationship to Tool Assignment (ADR-009)

The registry pattern and per-phase tool assignment (see [ADR-009](./009-tool-assignment-as-phase-level-concern.md)) are complementary but separate concerns:

- **AgentRegistry** defines what an agent **supports**: binary path, model, stream format, output extraction strategy, and default mode-specific flags via `AgentConfig.flags[AgentMode]`. These are intrinsic properties of the agent CLI.
- **ToolSet** defines what an agent is **allowed to use** in a specific phase: which internal tools, which MCP server tools, what permission level, and how many conversation turns. These are extrinsic, context-dependent constraints set by the pipeline definition.

The `AgentConfig.flags[mode]` field provides **default** tool-related flags for each agent mode (e.g., Claude in PLAN mode defaults to read-only tools). However, `Phase.tools` and `Phase.agent_tools` can **override** these defaults per phase. The resolution order is:

```
Phase.agent_tools[agent_name]  >  Phase.tools  >  AgentConfig.flags[mode]  >  ToolSet defaults
```

This separation means:
- The registry remains stable when pipeline definitions change
- Pipeline authors can restrict or expand tool access without modifying agent configurations
- The same agent can have different tool access in different phases of the same pipeline (e.g., Claude with read-only tools in a research phase, full-access tools in an implementation phase)

### When to Revisit This Decision

- If the number of supported agents exceeds 15 (consider plugin system for distribution)
- If community contributions of agent configurations become common (consider a registry service or published TOML collections)
- If agents start requiring behavioral differences beyond configuration (e.g., custom authentication flows) that cannot be expressed as data
- If TOML-based configuration becomes insufficient for complex agent setups (e.g., conditional flags based on runtime context)

## Consequences

### Positive

- Core has no stale hardcoded agent defaults to maintain
- Adding a new agent that uses a supported stream format requires only a complete TOML entry or programmatic registration
- Overriding registered configs uses `with_overrides()` — no forking orchcore
- Central `get()` lookup provides single point of agent resolution for all components
- `AgentConfig` Pydantic model validates configuration at load time
- `list_agents()`, `available()`, and `validate()` enable tooling and user-facing agent discovery
- `register()` enables dynamic configuration in tests and advanced workflows

### Negative

- Consumers must provide agent configuration before use
- New stream formats (not just agents) still require a Python parser implementation
- Complete TOML entries are more verbose than sparse configuration files

### Neutral

- Registry pattern is a well-understood design pattern — no learning curve
- Supported stream parsers cover the major AI coding agent CLIs, but registry data remains consumer-owned

## Validation and Monitoring

| Success Metric | Target | How to Measure |
|---|---|---|
| Programmatic registration | Registered agents resolve via `get()` | Unit test: `register(config)`, then `registry.get(config.name)` |
| Unknown agent behavior | Unknown names raise `KeyError` | Unit test: `registry.get("missing")` raises `KeyError` |
| New agent via TOML | Adding a complete agent config in TOML makes it available via `get()` | Unit test: define `custom-agent` in TOML, resolve it |
| Atomic TOML loading | Invalid entries do not partially mutate the registry | Unit test with invalid config and pre-existing registry |
| Runtime overrides | `with_overrides()` returns patched configs without mutating the original registry | Unit test: override `model`, compare original and patched registries |
| Availability reporting | `available()` and `validate()` reflect registered binaries on `PATH` | Unit tests with PATH-controlled fake binaries |

**Review Schedule:**
- On each new major agent CLI launch: add a parser only when its stream format is unsupported
- Quarterly: Review user feedback on agent configuration experience
- Annually: Reassess registry vs. plugin approach

## Related Decisions

- **ADR-001:** [Extract reusable orchestration core](./001-extract-reusable-orchestration-core.md) — registry is a core component
- **ADR-004:** [Composable stream pipeline](./004-composable-stream-processing-pipeline.md) — StreamFormat from registry drives parser selection
- **ADR-005:** [Multi-source layered configuration](./005-multi-source-layered-configuration.md) — settings-level agent overrides can be applied with `with_overrides()`
- **ADR-006:** [Pydantic for all data models](./006-pydantic-for-all-data-models.md) — AgentConfig is a Pydantic model
- **ADR-009:** [Tool assignment as phase-level concern](./009-tool-assignment-as-phase-level-concern.md) — ToolSet complements registry by defining per-phase tool access

## References

- [Registry pattern](https://martinfowler.com/eaaCatalog/registry.html)
- [Python importlib.metadata entry points](https://docs.python.org/3/library/importlib.metadata.html#entry-points) (rejected alternative mechanism)
- [TOML specification](https://toml.io/)

## Document History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-03-25 | Abdelaziz Abdelrasol | Initial version (ACCEPTED) |
| 1.1 | 2026-03-25 | Abdelaziz Abdelrasol | Added relationship to tool assignment (ADR-009); added ADR-009 to related decisions |
| 1.2 | 2026-06-10 | Abdelaziz Abdelrasol | Refreshed implementation details for registry-as-data, atomic TOML loading, and `with_overrides()` |
| 1.3 | 2026-07-02 | Abdelaziz Abdelrasol | Amendment note: ADR-011 replaced `AgentMode` with consumer-defined flag profiles |
