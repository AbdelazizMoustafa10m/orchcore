# Agent Registry

This guide covers how to configure agents via TOML, register them at runtime, and add support for new agent CLIs without writing any code.

## Overview

The `AgentRegistry` stores `AgentConfig` definitions and provides runtime lookup by name. Agents are defined as data — TOML files or Python dicts — not hardcoded classes. Adding a new agent CLI to orchcore requires only a TOML entry.

## Agent Configuration (TOML)

### Full Example

```toml
[agents.claude]
binary = "claude"
model = "claude-sonnet-4-20250514"
subcommand = "-p"
stream_format = "claude"
stall_timeout = 300.0
deep_tool_timeout = 600.0

[agents.claude.flags]
plan = ["--think", "--verbose"]
fix = ["--fix-mode"]
audit = ["--think", "--verbose"]
review = ["--think", "--verbose"]

[agents.claude.env_vars]
ANTHROPIC_API_KEY = "${ANTHROPIC_API_KEY}"

[agents.claude.output_extraction]
strategy = "jq_filter"
jq_expression = ".content[0].text"
```

### Field Reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `binary` | `str` | Yes | Path or name of the agent CLI executable |
| `model` | `str` | Yes | Model identifier passed to the agent |
| `subcommand` | `str` | Yes | How to pass the prompt (e.g., `"-p"` for Claude, `""` for stdin) |
| `stream_format` | `str` | Yes | JSONL format: `claude`, `codex`, `opencode`, `gemini`, `copilot` |
| `stall_timeout` | `float` | No | Seconds before stall detection (default: 300) |
| `deep_tool_timeout` | `float` | No | Timeout for deep tools like Exa/Tavily (default: 600) |
| `flags.<mode>` | `list[str]` | No | Mode-specific CLI flags (modes: `plan`, `fix`, `audit`, `review`) |
| `env_vars` | `dict` | No | Environment variables to set for the subprocess |
| `output_extraction.strategy` | `str` | Yes | How to extract output: `jq_filter`, `direct_file`, `stdout_capture` |
| `output_extraction.jq_expression` | `str` | No | jq expression for `jq_filter` strategy |

### Agent Modes

Each agent supports four execution modes with mode-specific CLI flags:

| Mode | Purpose | Typical Flags |
|------|---------|---------------|
| `plan` | Implementation planning | `["--think", "--verbose"]` |
| `fix` | Applying fixes autonomously | `["--fix-mode"]` |
| `audit` | Code audit and analysis | `["--think", "--verbose"]` |
| `review` | Code review | `["--think", "--verbose"]` |

### Output Extraction Strategies

| Strategy | Description |
|----------|-------------|
| `jq_filter` | Extract text from JSONL using the `jq_expression`. Parsed natively — no `jq` binary required. |
| `direct_file` | Agent writes output to a file; orchcore reads it. |
| `stdout_capture` | Capture the full stdout as output. |

## Multi-Agent TOML

Define multiple agents in one file:

```toml
[agents.claude]
binary = "claude"
model = "claude-sonnet-4-20250514"
subcommand = "-p"
stream_format = "claude"

[agents.claude.flags]
plan = ["--think", "--verbose"]
fix = ["--fix-mode"]

[agents.claude.output_extraction]
strategy = "jq_filter"
jq_expression = ".content[0].text"

[agents.codex]
binary = "codex"
model = "o3"
subcommand = ""
stream_format = "codex"

[agents.codex.flags]
plan = ["--approval-mode", "suggest"]
fix = ["--approval-mode", "full-auto"]

[agents.codex.output_extraction]
strategy = "stdout_capture"

[agents.gemini]
binary = "gemini"
model = "gemini-2.5-pro"
subcommand = ""
stream_format = "gemini"

[agents.gemini.flags]
plan = []
fix = []

[agents.gemini.output_extraction]
strategy = "stdout_capture"
```

## Runtime Usage

### Loading from TOML

```python
from pathlib import Path
from orchcore.registry import AgentRegistry

registry = AgentRegistry()
registry.load_from_toml(Path("agents.toml"))

# Lookup by name
claude = registry.get("claude")
print(claude.binary)        # "claude"
print(claude.stream_format) # StreamFormat.CLAUDE
```

### Programmatic Registration

```python
from orchcore.registry import AgentConfig, AgentMode, OutputExtraction
from orchcore.stream import StreamFormat

config = AgentConfig(
    name="my-agent",
    binary="/usr/local/bin/my-agent",
    model="my-model-v1",
    subcommand="--prompt",
    flags={AgentMode.PLAN: ["--verbose"], AgentMode.FIX: ["--auto"]},
    stream_format=StreamFormat.CLAUDE,  # If compatible with Claude's JSONL format
    output_extraction=OutputExtraction(strategy=OutputExtraction.Strategy.STDOUT_CAPTURE),
)

registry.register(config)
```

### Checking Availability

```python
# List all registered agents
registry.list_agents()  # ["claude", "codex", "gemini"]

# List agents whose binary is on PATH
registry.available()  # ["claude", "codex"]  (gemini not installed)

# Validate a list of agent names
missing = registry.validate(["claude", "codex", "unknown"])
# Returns ["unknown"] — agents not registered or not on PATH
```

## ToolSet — Phase-Level Tool Configuration

Tools available to an agent are defined at the phase level, not the agent level. This is a deliberate design decision ([ADR-009](../architecture/adrs/009-tool-assignment-as-phase-level-concern.md)).

```python
from orchcore.registry import ToolSet

# Read-only planning phase
planning_tools = ToolSet(
    internal=["Read", "Glob", "Grep"],
    mcp=[],
    permission="read-only",
    max_turns=15,
)

# Write-enabled execution phase
execution_tools = ToolSet(
    internal=["Read", "Write", "Edit", "Bash"],
    mcp=["mcp__exa__web_search_exa"],
    permission="workspace-write",
    max_turns=25,
)
```

### ToolSet Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `internal` | `list[str]` | `[]` | Agent-native tools (e.g., Read, Write, Edit, Bash) |
| `mcp` | `list[str]` | `[]` | MCP server tools (e.g., Exa, Tavily) |
| `permission` | `str` | `"read-only"` | Write access level: `read-only`, `workspace-write`, `full-access` |
| `max_turns` | `int` | `25` | Maximum conversation turns for the agent |

### Tool Resolution Order

When running an agent within a phase, the effective toolset is resolved via:

```
Phase.agent_tools[agent]  >  explicit toolset arg  >  Phase.tools  >  AgentConfig.flags[mode]  >  defaults
```

This allows fine-grained control: the planning phase can be read-only while the execution phase gets write access, and within a phase, different agents can have different tool sets.

## Adding a New Agent CLI

To add support for a completely new agent CLI:

1. **Determine the stream format** — if it outputs JSONL compatible with one of the 5 supported formats, use that format.
2. **Add a TOML entry** — define the agent's binary, model, flags, and output extraction strategy.
3. **Test** — run the agent through a simple pipeline to verify stream parsing works.

If the agent's JSONL format is incompatible with all 5 supported formats, a new `StreamFormat` and parser must be added to `orchcore.stream` — but this is rare since most AI agent CLIs follow similar patterns.

## Related

- [Configuration Reference](../reference/configuration.md) — per-agent overrides in settings
- [Stream Events Reference](../reference/stream-events.md) — `StreamFormat` enum
- [ADR-007: Registry pattern for agent management](../architecture/adrs/007-registry-pattern-for-agent-management.md)
- [ADR-009: Tool assignment as phase-level concern](../architecture/adrs/009-tool-assignment-as-phase-level-concern.md)
