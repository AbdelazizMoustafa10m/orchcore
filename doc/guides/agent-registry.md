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
max_runtime = 1800.0
kill_on_stall = false
env_policy = "filtered"
env_passlist = ["ANTHROPIC_API_KEY"]

# Flag profiles: the names below are YOUR project's workflow vocabulary,
# not an orchcore concept — "plan"/"fix" are examples, not a fixed set.
[agents.claude.flags]
plan = ["--think", "--verbose"]
fix = ["--fix-mode"]

# Optional: env_vars values are literal TOML strings. orchcore does not
# expand ${VAR}; load secrets in your own config layer before passing them.

[agents.claude.output_extraction]
strategy = "jq_filter"
jq_expression = ".content[0].text"
```

### Field Reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `binary` | `str` | Yes | Path or name of the agent CLI executable |
| `model` | `str` | Yes | Model identifier passed to the agent |
| `subcommand` | `str` | Yes | Agent CLI argument used before the prompt (for example, `"-p"` for Claude or `"exec"` for Codex). An empty string is omitted from the command — it never becomes a literal `''` argument. |
| `stream_format` | `str` | Yes | JSONL format: `claude`, `codex`, `opencode`, `gemini`, `copilot` |
| `prompt_via` | `"argv" \| "stdin"` | No | How the prompt reaches the CLI (default: `argv`). `stdin` keeps the prompt out of argv and pipes it to the child's stdin. |
| `stdin_sentinel` | `str \| None` | No | Argv placeholder appended instead of the prompt under `prompt_via = "stdin"` (for example, `"-"` for `codex exec -`). |
| `stall_timeout` | `float` | No | Seconds before stall detection (default: 300) |
| `deep_tool_timeout` | `float` | No | Timeout for deep tools like Exa/Tavily (default: 600) |
| `max_runtime` | `float \| None` | No | Hard wall-clock cap for the subprocess; `None` disables enforcement |
| `kill_on_stall` | `bool` | No | Terminate the process tree when a stall event is detected |
| `env_policy` | `"filtered" \| "inherit" \| "clean"` | No | Environment source policy for the subprocess (default: `filtered`) |
| `env_passlist` | `list[str]` | No | Case-insensitive regex allowlist that re-admits filtered environment names |
| `version_command` | `list[str]` | No | Arguments that print the CLI version (default: `["--version"]`; `[]` disables the check) |
| `compatible_versions` | `list[str]` | No | Version specifiers declared as known good (e.g. `[">=2.1.112,<3"]`) |
| `incompatible_versions` | `list[table]` | No | Known-bad ranges, each `{ spec = "...", reason = "..." }` |
| `flags.<profile>` | `list[str]` | No | Named flag profile: a consumer-defined name mapped to this agent's CLI flags (see [Flag Profiles](#flag-profiles)) |
| `env_vars` | `dict` | No | Environment variables to set for the subprocess |
| `output_extraction.strategy` | `str` | Yes | How to extract output: `jq_filter`, `direct_file`, `stdout_capture` |
| `output_extraction.jq_expression` | `str` | No | jq expression for `jq_filter` strategy |

### Flag Profiles

A flag profile is a named bundle of CLI flags on an agent. The *names* are
defined entirely by the consuming project — they are workflow vocabulary
(`plan`/`fix` in a planning tool, `research`/`draft`/`art` in a publishing
pipeline, `audit` in a compliance tool), and orchcore attaches no meaning to
them beyond looking them up ([ADR-011](../architecture/adrs/011-consumer-defined-flag-profiles.md)).
The profile mapping is the translation table from your vocabulary to each
CLI's dialect: the same profile name can map to `["--think"]` for Claude and
`["--reasoning", "high"]` for Codex.

A profile is selected per phase (`Phase.flag_profile`) or as a pipeline-wide
default (`run_pipeline(flag_profile=...)`); the phase value wins. With no
selection, **no** profile flags are applied — there is no implicit default.
Selecting a profile an agent does not define logs a warning and applies no
flags for that agent.

Keep behavioral flags (thinking, verbosity, effort) in profiles; tool access
and permissions belong in a [`ToolSet`](#toolset--phase-level-tool-configuration),
which composes *with* profiles rather than replacing them. When a ToolSet is
in effect, profile flags in the ToolSet-managed domain — tool allowlists,
sandbox/permission/approval flags (including bypasses like `--yolo` or
`--dangerously-skip-permissions`), and stream-output format — are **dropped
with a warning**, because several CLIs hard-fail on duplicated singleton
flags and a bypass flag cannot be neutralized by flags appended after it.
Without a ToolSet, profile flags pass through verbatim. Malformed profile
names (empty or flag-like, e.g. `"--think"`) are rejected at the API
boundary rather than warned about at runtime.

Projects that want compile-time safety over their vocabulary can define their
own enum — `StrEnum` members are strings and pass straight through:

```python
from enum import StrEnum

class Mode(StrEnum):          # lives in YOUR project, not orchcore
    PLAN = "plan"
    FIX = "fix"

phase = Phase(name="planning", agents=("claude",), flag_profile=Mode.PLAN)
```

### Output Extraction Strategies

| Strategy | Description |
|----------|-------------|
| `jq_filter` | Extract text from JSONL using the `jq_expression`. Parsed natively — no `jq` binary required. |
| `direct_file` | Agent writes output to a file; orchcore reads it. |
| `stdout_capture` | Capture the full stdout as output. |

### Environment Policy

Agent subprocesses default to `env_policy = "filtered"`. This keeps normal process basics like `PATH` and `HOME`, but strips common credential and agent configuration families such as `ANTHROPIC_*`, `OPENAI_*`, `GITHUB_*`, `AWS_*`, proxy variables, and telemetry variables. Values in `env_vars` are always applied last.

Use one of these migration paths when an agent genuinely needs an ambient variable:

```toml
# Keep the old full-inheritance behavior for this agent.
env_policy = "inherit"

# Or keep the filtered default and re-admit specific names.
env_passlist = ["ANTHROPIC_API_KEY"]

# Or pass explicit literal values from your own config layer.
[agents.claude.env_vars]
ANTHROPIC_API_KEY = "..."
```

`env_policy = "clean"` starts from a minimal platform environment. It is useful for reproducibility checks, but it is not a full hermetic home-directory sandbox.

### Version Compatibility

Agent CLIs release daily, and a stream-format change in a new CLI version otherwise surfaces only as inscrutable parse warnings. Version expectations are registry data:

```toml
[agents.claude]
# ...
version_command = ["--version"]            # default; [] disables checking
compatible_versions = [">=2.1.112,<3"]

[[agents.claude.incompatible_versions]]
spec = "<=2.0.0"
reason = "stream-json v1 format; https://github.com/anthropics/claude-code/issues/NNN"
```

Specifier grammar: comma-separated AND of `==`, `!=`, `>=`, `<=`, `>`, `<` clauses; `==`/`!=` accept a trailing `.*` wildcard (`"==2.1.*"`); list entries combine as OR. Known-incompatible ranges win over compatible ones.

How the check behaves at runtime:

- It runs **once per binary path per process** (cached, including failures) and is **advisory**: it never fails or delays the run beyond a hard 10-second timeout.
- The version subprocess crosses the same explicit boundary as agent runs: filtered environment per the agent's `env_policy`, the run's explicit working directory, no stdin.
- Logging is calibrated: known-compatible versions log at DEBUG, known-incompatible at WARNING (with the recorded reason), versions outside declared ranges at INFO, and undeclared setups at DEBUG.
- The detected version lands on `AgentResult.agent_version` (`None` when detection is disabled or fails).

Treat the ranges as maintained data: every `incompatible_versions` entry should carry a `reason` linking to the upstream issue or changelog entry that motivated it.

### Prompt Transport (`prompt_via`)

By default the prompt travels as a command-line argument (`prompt_via = "argv"`). For very large prompts this risks `ARG_MAX` (POSIX) / 32K `CreateProcess` (Windows) limits, and the prompt content is visible in local process listings. Setting `prompt_via = "stdin"` omits the prompt from argv, opens a stdin pipe, and writes the encoded prompt concurrently with stream consumption (avoiding pipe-buffer deadlocks), then closes stdin.

Per-CLI stdin support:

| CLI | Configuration | Notes |
|-----|---------------|-------|
| Claude Code | `subcommand = "-p"`, `prompt_via = "stdin"` | `-p` accepts the prompt from piped stdin; capped at 10 MB as of v2.1.128 — reference files in the prompt for larger contexts. |
| Codex CLI | `subcommand = "exec"`, `prompt_via = "stdin"`, `stdin_sentinel = "-"` | `codex exec -` reads the full prompt from stdin; the `-` placeholder must be the prompt argument. |
| Others | keep `prompt_via = "argv"` | Use argv until stdin support is verified for the specific CLI. |

orchcore drives non-interactive CLI modes only: CLIs that expect stdin to stay open for interaction are out of scope — stdin is closed after the prompt is written.

```toml
[agents.codex]
binary = "codex"
model = "gpt-5.2-codex"
subcommand = "exec"
stream_format = "codex"
prompt_via = "stdin"
stdin_sentinel = "-"
```

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
subcommand = "exec"
stream_format = "codex"

# Behavioral flags only — tool access, sandbox, and approval flags belong
# in each phase's ToolSet.
[agents.codex.flags]
plan = ["-c", "model_reasoning_effort=high"]
fix = []

[agents.codex.output_extraction]
strategy = "stdout_capture"

[agents.gemini]
binary = "gemini"
model = "gemini-2.5-pro"
subcommand = "-p"
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

Loading is **atomic**: every entry is parsed and validated before any is
registered. With the default `on_error="raise"`, a file containing invalid
entries raises a single `ValueError` naming *all* offenders and leaves the
registry untouched. Pass `on_error="skip"` to register the valid entries and
log a warning per invalid one (the pre-1.0 lenience for non-table entries).

TOML values are used **literally** — no `${VAR}` environment-variable
interpolation is performed; a value like `"${ANTHROPIC_API_KEY}"` would reach
the subprocess as that exact string. For ambient credentials use
`env_policy`/`env_passlist` (see [Environment Policy](#environment-policy)),
or resolve values in your own configuration layer before registering.

### Programmatic Registration

```python
from orchcore.registry import AgentConfig, OutputExtraction
from orchcore.stream import StreamFormat

config = AgentConfig(
    name="my-agent",
    binary="/usr/local/bin/my-agent",
    model="my-model-v1",
    subcommand="--prompt",
    flags={"plan": ["--verbose"], "fix": ["--auto"]},  # your project's vocabulary
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
Phase.agent_tools[agent]  >  explicit toolset arg  >  Phase.tools  >  none
```

This allows fine-grained control: the planning phase can be read-only while the execution phase gets write access, and within a phase, different agents can have different tool sets.

Flag profiles are independent of this resolution: a selected profile's flags
are always applied, and the ToolSet translation (when one resolves) is
appended after them.

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
