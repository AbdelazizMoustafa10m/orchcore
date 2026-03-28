---
id: ADR-009
title: Tool assignment as a phase-level concern
status: ACCEPTED
date: 2026-03-25
decision_makers: [Abdelaziz Abdelrasol]
consulted: []
informed: []
confidence: HIGH
tags: [tools, phases, security, mcp, configuration, toml]
related_decisions: [ADR-001, ADR-007]
supersedes: []
superseded_by: []
---

# ADR-009: Tool assignment as a phase-level concern

## Status

ACCEPTED

## Context and Problem Statement

orchcore must control which tools are available to agents at each step of a pipeline. The current design assigns tools via `AgentConfig.flags[AgentMode]`, which maps agent modes (PLAN, FIX, AUDIT, REVIEW) to CLI flags. This is too coarse — in practice, tools vary **per phase** within the same mode, not just per mode. Evidence from all four source systems demonstrates this gap:

- **Articles** (8-phase pipeline): Phase 1 (research) uses `Read + Tavily MCP + Exa MCP` for web research. Phase 2 (draft) uses `Read, Write` for content writing. Phase 5 (art generation) uses `Read, Write, Bash, Edit` for image generation scripts. All three phases run Claude in FIX mode, but each needs fundamentally different tools.
- **Finvault** (audit pipeline): Audit phases use read-only tools (`Read, Glob, Grep`) to analyze code without modification. Fix phases use full-access tools (`Read, Write, Edit, Bash`) to apply corrections. The agent mode alone cannot express this distinction when the same agent (Claude) is used in both.
- **Raven** (autonomous development): Implementation phases need `Edit, Write, Read, Bash` for code changes. Review phases need only `Read, Glob, Grep` to inspect results without accidentally modifying them. Security requires that review phases cannot write.
- **Planora** (planning pipeline): Plan phase needs read-only tools for analysis. Refine phase could need Write access to update plan documents. Audit phases should be strictly read-only regardless of the agent used.

Additionally, MCP (Model Context Protocol) servers are an emerging concern. Agent CLIs increasingly support external tool servers (Tavily for search, Exa for web research, custom internal tools). These MCP tools must be assignable per phase alongside internal tools — a research phase might use Tavily and Exa, while a coding phase should not have access to web search tools.

The fundamental problem is: **agent modes are an intrinsic property of the agent, but tool access is an extrinsic, context-dependent constraint set by the pipeline definition.** These are separate concerns that should be modeled separately.

### Business Context

- MCP server adoption is growing rapidly — tools like Tavily, Exa, and custom internal servers are becoming standard in AI workflows
- Security-conscious teams require enforceable tool restrictions: read-only phases must not have write tools, regardless of what the agent supports
- Pipeline definitions should be self-documenting: looking at a phase's TOML configuration should show exactly which tools are available
- Different consuming projects need different tool configurations for the same agents — Planora might restrict Claude to read-only in audit phases, while Raven gives Claude full access in implementation phases

## Decision Drivers

| Driver | Priority | Why It Matters |
|---|---|---|
| Flexibility: different phases need different tools | Critical | Evidence from 4 source systems shows per-mode assignment is insufficient |
| Security: read-only phases must not have write tools | Critical | Enforceable tool restrictions prevent accidental modifications in audit/review phases |
| MCP server support | High | External tool servers are a growing concern; must be assignable per phase alongside internal tools |
| Configurability: TOML-based tool assignment | High | Aligns with orchcore's configuration-over-code philosophy (ADR-005, ADR-007) |
| Backward compatibility | High | Existing `AgentConfig.flags[mode]` must continue to work as a default fallback |
| Per-agent overrides within a phase | Medium | Parallel phases may run multiple agents with different tool requirements (e.g., Codex needs `workspace-write`, Gemini needs `read-only`) |

## Considered Options

### Option 1: Phase-level ToolSet with per-agent overrides (CHOSEN)

**Overview:** Introduce a `ToolSet` Pydantic model that defines the tools available in a specific execution context. The `Phase` model gains two new fields: `tools` (default ToolSet for all agents in the phase) and `agent_tools` (per-agent overrides within the phase). A layered resolution order determines the effective tools for each agent invocation.

**ToolSet model:**
```python
class ToolSet(BaseModel):
    """Tools available for a specific execution context."""

    # Agent-internal tools (native to the agent CLI)
    internal: list[str] = []
    # e.g., ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]

    # MCP server tools (external tool servers)
    mcp: list[str] = []
    # e.g., ["mcp__tavily__tavily_search", "mcp__exa__web_search_exa"]

    # Permission level (agent-specific translation)
    permission: str = "read-only"
    # e.g., "read-only", "workspace-write", "full-access"

    # Agent conversation depth
    max_turns: int = 25
```

**Updated Phase model:**
```python
class Phase(BaseModel):
    name: str
    agents: list[str]
    parallel: bool = False
    required: bool = True
    depends_on: list[str] = []

    # Tool configuration
    tools: ToolSet | None = None            # Default tools for ALL agents in this phase
    agent_tools: dict[str, ToolSet] = {}    # Per-agent override within this phase
```

**Resolution order (highest priority wins):**
```
Phase.agent_tools["claude"]  >  Phase.tools  >  AgentConfig.flags[mode]  >  defaults
```

**TOML configuration:**
```toml
[[phases]]
name = "research"
agents = ["claude"]

[phases.tools]
internal = ["Read", "Glob", "Grep"]
mcp = ["mcp__tavily__tavily_research", "mcp__exa__web_search_exa"]
permission = "read-only"
max_turns = 40

[[phases]]
name = "audit"
agents = ["gemini", "codex"]
parallel = true

[phases.tools]
internal = ["Read", "Glob", "Grep", "Agent"]
permission = "read-only"
max_turns = 50

# Per-agent override within audit phase
[phases.agent_tools.codex]
permission = "workspace-write"
max_turns = 30
```

**AgentRunner translation:** The AgentRunner translates ToolSet into agent-specific CLI flags:
- **Claude**: `--allowedTools "Read,Write,mcp__tavily__tavily_search" --max-turns 40`
- **Codex**: `--sandbox workspace-write`; MCP tools configured via `config.toml` `[mcp_servers.*]` sections (not via CLI flags)
- **Gemini**: `--yolo` (full access) or default sandboxed mode
- **Copilot**: Permission flags (evolving)
- **OpenCode**: Model-specific sandbox flags; MCP tools configured via `opencode.json` `mcpServers` / `mcp` sections (not via CLI flags)

This translation lives in the runner, not the registry. The registry defines what flags an agent SUPPORTS; the runner builds the actual command using ToolSet + AgentConfig.

**Pros:**
- Granular control: each phase can specify exactly which tools are available
- MCP-aware: external tool servers are a first-class part of the tool configuration
- Security-enforceable: CLI flags are enforced by the agent runtime, not just prompt instructions
- TOML-configurable: pipeline authors define tool access in configuration, not code
- Backward-compatible: `Phase.tools` is optional; when absent, existing `AgentConfig.flags[mode]` behavior applies
- Per-agent overrides: parallel phases can give different agents different tool access

**Cons:**
- Each agent requires a translation layer in AgentRunner to map ToolSet to CLI flags
- More configuration surface area — pipeline TOML files become more detailed
- ToolSet fields (internal, mcp, permission) have different semantics per agent, which may confuse users

**Risk Assessment:**

| Risk Type | Level | Detail |
|---|---|---|
| Technical | Low | ToolSet is a straightforward Pydantic model; translation to CLI flags is a simple conditional per agent |
| Ecosystem | Medium | MCP tool naming conventions are not yet fully standardized; may need updates as MCP matures |
| Adoption | Low | ToolSet is optional — existing pipelines work without it, adopting it is incremental |

**Trade-offs:**
- We gain fine-grained, enforceable, configurable tool access per phase, accepting a per-agent translation layer and increased configuration surface

---

### Option 2: Tool assignment only in AgentConfig.flags (current design, status quo)

**Overview:** Keep the current approach where tools are assigned via `AgentConfig.flags[AgentMode]`. Each agent mode (PLAN, FIX, AUDIT, REVIEW) has a fixed set of CLI flags that include tool restrictions.

**Pros:**
- Simple — no new models or resolution logic
- Already implemented (zero effort)
- Agent modes are a natural categorization

**Cons:**
- Too coarse — cannot express "research phase with web tools" vs. "draft phase with write tools" when both use FIX mode
- No MCP support — MCP tools cannot be assigned per phase
- Requires creating artificial agent modes to represent different tool sets (e.g., FIX_RESEARCH, FIX_WRITE, FIX_ART)
- Mode proliferation would break the clean AgentMode enum

**Why not chosen:**
- Evidence from all four source systems shows that tool requirements vary within the same agent mode. Creating artificial modes to work around this limitation would complicate the enum, the registry, and every consuming project's pipeline definitions. Tool assignment is an orthogonal concern that should be modeled separately from agent modes.

---

### Option 3: Tool assignment via prompt instructions only

**Overview:** Instead of CLI flags, include tool restriction instructions in the prompt text (e.g., "You may only use Read, Glob, and Grep tools. Do not use Write or Edit.").

**Pros:**
- Zero changes to orchcore's data model or runner
- Works with any agent that follows instructions
- No per-agent translation needed

**Cons:**
- Not enforceable — agents may ignore prompt instructions, especially under complex reasoning
- No MCP tool control — prompt instructions cannot prevent an agent from calling an MCP server tool
- Security-critical restrictions (read-only in audit phases) must be enforceable, not advisory
- Claude Code's `--allowedTools` flag provides hard enforcement; prompt instructions do not

**Why not chosen:**
- Tool restrictions are a security concern. Prompt-based restrictions are advisory and can be ignored by the agent. CLI flags like `--allowedTools` and `--sandbox` are enforced by the agent runtime and cannot be bypassed. For audit and review phases where preventing writes is critical, only CLI-enforced restrictions are acceptable.

---

### Option 4: Separate ToolRegistry alongside AgentRegistry

**Overview:** Create a `ToolRegistry` that catalogs all available tools (internal and MCP), their capabilities, and their compatibility with each agent. Phases reference tools by name from this registry.

**Pros:**
- Full catalog of available tools with metadata
- Can validate tool names at pipeline load time
- Could support tool discovery and documentation

**Cons:**
- Over-engineering for the current scale (5 agents, ~20 tools)
- Tools are agent-specific — a "Read" tool in Claude is fundamentally different from file reading in Codex
- Adds a new component, new models, and new configuration surface for marginal benefit
- Validation can be done simply by checking tool names against known lists, without a full registry

**Why not chosen:**
- A ToolRegistry would be valuable if orchcore managed hundreds of tools across dozens of agents, or if tools had complex metadata (capabilities, version requirements, dependencies). At the current scale, a simple ToolSet model with lists of tool names provides equivalent functionality with dramatically less complexity. If tool management complexity grows, this option can be revisited.

## Decision

**We have decided to introduce ToolSet as a first-class Pydantic model that defines the tools available to an agent in a specific execution context. The Phase model gains `tools` (phase-level default) and `agent_tools` (per-agent overrides) fields. The AgentRunner translates ToolSet into agent-specific CLI flags. A layered resolution order (phase agent override > phase default > agent mode flags > built-in defaults) determines the effective tools for each agent invocation.**

### Prerequisite: MCP Server Pre-Configuration

orchcore does **not** manage MCP server installation or lifecycle on agent CLIs. Consuming projects must ensure that any MCP servers referenced in `ToolSet.mcp` are pre-configured on the target agent CLI before pipeline execution. Each agent has its own MCP configuration mechanism:

- **Claude**: Settings or project-level `.mcp.json` — orchcore controls access to already-configured MCP tools via `--allowedTools`.
- **Codex**: `~/.codex/config.toml` `[mcp_servers.*]` sections or `codex mcp add` CLI command.
- **Gemini**: Gemini CLI config file.
- **OpenCode**: `opencode.json` `mcpServers` / `mcp` sections or `opencode mcp add` CLI command.

This is a deliberate design boundary: orchcore orchestrates agent invocations and controls which tools are *permitted* per phase, but the underlying MCP server setup is the responsibility of the deployment environment. This avoids coupling orchcore to each agent's config file format and keeps MCP server credentials/secrets outside of pipeline TOML files.

### Implementation Details

- `ToolSet` is a Pydantic BaseModel with four fields: `internal` (list of internal tool names), `mcp` (list of MCP tool names), `permission` (string permission level), and `max_turns` (int conversation depth limit)
- `Phase.tools` is an optional ToolSet that applies to all agents in the phase when no agent-specific override exists
- `Phase.agent_tools` is a dict mapping agent names to ToolSet instances, providing per-agent overrides within a phase
- `PhaseRunner` resolves the effective ToolSet before calling `AgentRunner.run()`, passing it as an optional parameter
- `AgentRunner.run()` accepts an optional `toolset: ToolSet | None` parameter; when provided, it translates the ToolSet to CLI flags instead of using `AgentConfig.flags[mode]`
- Translation logic is implemented per agent in the runner module (not the registry) as a mapping from ToolSet fields to CLI flags
- When `toolset` is `None`, the existing `AgentConfig.flags[mode]` behavior is preserved (backward compatibility)

### When to Revisit This Decision

- If MCP tool naming conventions are standardized (may need to update ToolSet.mcp format)
- If agents start supporting richer tool configuration (beyond allowed lists and permissions)
- If the number of distinct tool configurations per pipeline exceeds 20 (consider a ToolRegistry for catalog/validation)
- If cross-phase tool dependencies emerge (e.g., "phase 3 should have all tools from phase 2 plus Write")

## Consequences

### Positive

- Fine-grained tool control: each phase can specify exactly which internal tools and MCP servers are available
- Security-enforceable: CLI flags are enforced by the agent runtime, preventing unauthorized tool use in read-only phases
- TOML-configurable: pipeline authors define tool access in configuration files, not code
- Backward-compatible: existing pipelines without ToolSet continue to work using AgentConfig.flags[mode]
- MCP-aware: external tool servers are first-class citizens in the tool configuration model
- Per-agent flexibility: parallel phases can give different agents different tool access (e.g., Codex with workspace-write, Gemini with read-only)
- Self-documenting: looking at a phase's TOML configuration shows exactly which tools are available

### Negative

- Each agent requires a translation layer in AgentRunner to map ToolSet to agent-specific CLI flags
- Increased configuration surface: pipeline TOML files may become more verbose with per-phase tool specifications
- ToolSet field semantics vary by agent: "permission" means different things for Claude vs. Codex vs. Gemini
- MCP tool names use a flat namespace (`mcp__server__tool`) that may not align with future MCP naming standards

### Neutral

- ToolSet is a simple Pydantic model with no complex behavior — it is purely a data container
- The resolution order (4 levels) is straightforward and follows the same pattern as orchcore's configuration layering (ADR-005)
- Translation tables for 5 agents are manageable; each is a small conditional mapping

## Validation and Monitoring

| Success Metric | Target | How to Measure |
|---|---|---|
| Phase-level tool override | ToolSet in Phase.tools overrides AgentConfig.flags[mode] | Unit test: set Phase.tools, verify AgentRunner receives correct CLI flags |
| Per-agent override | Phase.agent_tools overrides Phase.tools for specific agent | Unit test: set agent_tools for one agent, verify other agents use Phase.tools |
| Backward compatibility | Pipelines without ToolSet use AgentConfig.flags[mode] | Unit test: run pipeline with no ToolSet, verify existing behavior |
| TOML loading | ToolSet loads correctly from TOML phase configuration | Unit test: parse TOML with phase tools, verify ToolSet fields |
| Claude translation | ToolSet translates to --allowedTools and --max-turns | Unit test: verify command array for Claude with specific ToolSet |
| Codex translation | ToolSet translates to --sandbox flag | Unit test: verify command array for Codex with permission levels |
| MCP tools included | MCP tool names appear in agent's allowed tools list | Unit test: verify mcp tools appended to internal tools in --allowedTools |

**Review Schedule:**
- On each new agent CLI integration: implement translation layer for the new agent
- On MCP specification updates: review ToolSet.mcp naming convention
- Quarterly: Review consuming project feedback on tool configuration experience

## Related Decisions

- **ADR-001:** [Extract reusable orchestration core](./001-extract-reusable-orchestration-core.md) — ToolSet is part of the orchestration core's data model
- **ADR-007:** [Registry pattern for agent management](./007-registry-pattern-for-agent-management.md) — ToolSet complements the registry; registry defines what agents support, ToolSet defines what they are allowed to use per phase

## References

- [Model Context Protocol (MCP)](https://modelcontextprotocol.io/)
- [Claude Code --allowedTools documentation](https://docs.anthropic.com/en/docs/claude-code)
- [Codex --sandbox documentation](https://github.com/openai/codex)

## Document History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-03-25 | Abdelaziz Abdelrasol | Initial version (ACCEPTED) |
