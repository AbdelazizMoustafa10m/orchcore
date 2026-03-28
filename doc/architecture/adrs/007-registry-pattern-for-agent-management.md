---
id: ADR-007
title: Use registry pattern for agent management
status: ACCEPTED
date: 2026-03-25
decision_makers: [Abdelaziz Abdelrasol]
consulted: []
informed: []
confidence: HIGH
tags: [registry, agents, extensibility, configuration, toml]
related_decisions: [ADR-001, ADR-004, ADR-005, ADR-006, ADR-009]
supersedes: []
superseded_by: []
---

# ADR-007: Use registry pattern for agent management

## Status

ACCEPTED

## Context and Problem Statement

orchcore must manage configurations for multiple AI coding agent CLIs — Claude Code, Codex, Gemini, Copilot, OpenCode — and support adding new agents as the ecosystem evolves. Each agent has a distinct binary name, model identifier, subcommand structure, mode-specific CLI flags, JSONL stream format, output extraction strategy, environment variable requirements, and timeout settings.

The fundamental question is: how should orchcore represent and manage these per-agent configurations? The design must handle three scenarios:
1. Built-in agents (Claude, Codex, etc.) that work out of the box
2. User-customized agents (e.g., Claude with a different model or longer timeout)
3. Entirely new agents that orchcore doesn't know about yet

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
| Override built-in agent defaults | High | Users need to change models, timeouts, and flags without editing orchcore source |
| Central lookup by name | High | Runner, Pipeline, and Stream components need to resolve agent configs by name |
| Type-safe agent configuration | High | Invalid configs (wrong binary, missing flags) should fail fast with clear errors |
| Built-in defaults for known agents | High | Claude, Codex, etc. should work without any configuration |
| TOML-based configuration | Medium | Aligns with orchcore's configuration system (ADR-005) |

## Considered Options

### Option 1: Registry pattern with built-in defaults + TOML overrides (CHOSEN)

**Overview:** Implement an `AgentRegistry` that combines built-in agent configurations (defined as Python dicts in orchcore's source) with custom/override configurations loaded from TOML files. The registry provides `get(name)`, `list_available()`, and `register()` methods.

**Built-in agents defined in code:**
```python
BUILT_IN_AGENTS = {
    "claude": AgentConfig(
        name="claude",
        binary="claude",
        model="claude-sonnet-4-20250514",
        flags={
            AgentMode.PLAN: ["--allowedTools", "Read,Grep,Glob,LS"],
            AgentMode.FIX: [],
        },
        stream_format=StreamFormat.CLAUDE,
        output_extraction=OutputExtraction(strategy="jq_filter", jq_expression="..."),
        stall_timeout=300,
        deep_tool_timeout=600,
    ),
    "codex": AgentConfig(...),
    ...
}
```

**TOML overrides/additions:**
```toml
# Override built-in agent
[agents.claude]
model = "claude-opus-4-20250514"
stall_timeout = 400

# Add entirely new agent
[agents.custom-agent]
binary = "/usr/local/bin/my-agent"
model = "custom-model-v1"
stream_format = "claude"  # Reuse existing parser
```

**Pros:**
- Built-in agents work without any config file (zero-config)
- TOML overrides allow per-project customization without code changes
- New agents can be added via TOML alone — no orchcore release required
- Central `get(name)` lookup used by Runner, Pipeline, and Stream
- `AgentConfig` is a Pydantic model — type-safe with validation (ADR-006)
- `list_available()` enables tooling to show which agents are configured
- `register()` enables programmatic registration for testing and dynamic workflows
- TOML override merging: only specified fields are overridden, others keep defaults

**Cons:**
- Built-in agents in Python code creates a maintenance burden when defaults change
- New stream formats still require a parser implementation (TOML cannot add parsing logic)
- Registry is a global-ish singleton per orchestration session — must be thread-safe (asyncio single-thread makes this moot)

**Risk Assessment:**

| Risk Type | Level | Detail |
|---|---|---|
| Technical | Low | Registry pattern is well-established; AgentConfig is a straightforward Pydantic model |
| Schedule | Low | BUILT_IN_AGENTS dict and TOML loading are simple to implement |
| Ecosystem | Low | TOML parsing uses tomllib (stdlib); no third-party risk |

**Trade-offs:**
- We gain zero-config built-in agents and TOML-only extensibility for new agents, accepting that new stream formats still require a parser implementation in Python

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

**We have decided to use a registry pattern where built-in agent configurations are defined as Python dicts in orchcore's source, and custom/override configurations are loaded from TOML files. The AgentRegistry provides central lookup, listing, and programmatic registration.**

### Implementation Details

- `AgentRegistry.__init__(config_path: Path | None = None)`: loads built-in agents, then overlays TOML config from `config_path` (or from the config system's resolved TOML path)
- `AgentRegistry.get(name: str) -> AgentConfig`: returns merged config (built-in + TOML overrides). Raises `AgentNotFoundError` if unknown.
- `AgentRegistry.list_available() -> list[str]`: returns sorted list of all registered agent names
- `AgentRegistry.register(config: AgentConfig) -> None`: adds or replaces an agent config programmatically
- TOML merge logic: for each field in the TOML `[agents.X]` section, if the field is present, it overrides the built-in default. Missing fields retain the built-in value. For entirely new agents, all required fields must be present.
- Built-in agents: claude, codex, gemini, copilot, opencode
- Each built-in agent has a complete `AgentConfig` with sensible defaults for all fields

### Relationship to Tool Assignment (ADR-009)

The registry pattern and per-phase tool assignment (see [ADR-009](./009-tool-assignment-as-phase-level-concern.md)) are complementary but separate concerns:

- **AgentRegistry** defines what an agent **supports**: binary path, model, stream format, output extraction strategy, and default mode-specific flags via `AgentConfig.flags[AgentMode]`. These are intrinsic properties of the agent CLI.
- **ToolSet** defines what an agent is **allowed to use** in a specific phase: which internal tools, which MCP server tools, what permission level, and how many conversation turns. These are extrinsic, context-dependent constraints set by the pipeline definition.

The `AgentConfig.flags[mode]` field provides **default** tool-related flags for each agent mode (e.g., Claude in PLAN mode defaults to read-only tools). However, `Phase.tools` and `Phase.agent_tools` can **override** these defaults per phase. The resolution order is:

```
Phase.agent_tools[agent_name]  >  Phase.tools  >  AgentConfig.flags[mode]  >  built-in defaults
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

- Zero-config: built-in agents work immediately after `pip install orchcore`
- Adding a new agent requires only a TOML entry — no code changes, no orchcore release
- Overriding defaults (model, timeout, flags) is a TOML edit — no forking orchcore
- Central `get()` lookup provides single point of agent resolution for all components
- `AgentConfig` Pydantic model validates configuration at load time
- `list_available()` enables tooling and user-facing agent discovery
- `register()` enables dynamic configuration in tests and advanced workflows

### Negative

- New stream formats (not just agents) still require a Python parser implementation
- Built-in agent defaults in Python source need manual updates when agent CLIs change defaults
- TOML merge semantics (partial override) could confuse users expecting full replacement

### Neutral

- Registry pattern is a well-understood design pattern — no learning curve
- 5 built-in agents is a reasonable starting set covering the major AI coding agent CLIs

## Validation and Monitoring

| Success Metric | Target | How to Measure |
|---|---|---|
| Built-in agent resolution | All 5 built-in agents resolvable without any config file | Unit test: `registry.get("claude")` succeeds with no TOML |
| TOML override | Overriding a single field preserves all other defaults | Unit test: override `model`, check `stall_timeout` unchanged |
| New agent via TOML | Adding a complete agent config in TOML makes it available via `get()` | Unit test: define `custom-agent` in TOML, resolve it |
| Invalid config rejected | Missing required fields in TOML agent raise clear ValidationError | Unit test with incomplete agent config |
| List available | `list_available()` returns all built-in + TOML-configured agents | Unit test comparing expected vs. actual list |

**Review Schedule:**
- On each new major agent CLI launch: add built-in config and parser
- Quarterly: Review user feedback on agent configuration experience
- Annually: Reassess registry vs. plugin approach

## Related Decisions

- **ADR-001:** [Extract reusable orchestration core](./001-extract-reusable-orchestration-core.md) — registry is a core component
- **ADR-004:** [Composable stream pipeline](./004-composable-stream-processing-pipeline.md) — StreamFormat from registry drives parser selection
- **ADR-005:** [Multi-source layered configuration](./005-multi-source-layered-configuration.md) — TOML overrides loaded via config system
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
