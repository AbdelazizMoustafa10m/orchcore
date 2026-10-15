# Agent Registry

This guide describes how to define agents in TOML or Python, look them up at runtime, assign tools per phase, and add support for a new agent CLI.

## Overview

`AgentRegistry` stores `AgentConfig` definitions and looks them up by name. Agents are defined as data (TOML entries or `AgentConfig` objects), not as classes. A new agent CLI that uses one of the supported stream formats needs only a TOML entry.

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
| `subcommand` | `str` | Yes | Agent CLI argument used before the prompt (for example, `"-p"` for Claude or `"exec"` for Codex). An empty string is left out of the command instead of becoming a literal `''` argument. |
| `stream_format` | `str` | Yes | JSONL format: `claude`, `codex`, `opencode`, `antigravity`, `copilot` |
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

A **flag profile** is a named set of CLI flags on an agent. The consuming project
chooses the names as part of its workflow vocabulary (e.g., `plan` and `fix` in a
planning tool, `research`, `draft`, and `art` in a publishing pipeline, or `audit`
in a compliance tool). orchcore only looks the names up and attaches no other
meaning to them ([ADR-011](../architecture/adrs/011-consumer-defined-flag-profiles.md)).
Each agent maps a profile name to its own CLI flags, so the same profile can map
to `["--think"]` for Claude and `["--reasoning", "high"]` for Codex.

A profile is selected per phase (`Phase.flag_profile`) or as a pipeline-wide
default (`run_pipeline(flag_profile=...)`). If both are set, the phase value
wins. If neither is set, no profile flags are applied; there is no implicit
default. If an agent does not define the selected profile, orchcore logs a
warning and applies no profile flags for that agent.

Keep behavioral flags (thinking, verbosity, effort) in profiles. Tool access
and permissions belong in a [`ToolSet`](#phase-level-tool-configuration-toolset),
which is applied in addition to the profile, not instead of it. When a ToolSet
is in effect, profile flags that the ToolSet manages are dropped with a
warning. These are tool allowlists, sandbox, permission, and approval flags
(including bypasses such as `--yolo` or `--dangerously-skip-permissions`), and
the stream-output format. They are dropped because several CLIs fail on a
duplicated single-value flag, and flags appended later cannot undo a bypass
flag. Without a ToolSet, profile flags are passed through unchanged. Malformed
profile names (empty or flag-like, e.g., `"--think"`) raise an error as soon as
they are passed to `Phase`, `AgentConfig.flags`, or a run method, instead of
logging a warning later.

For static checking of profile names, a project can define its own enum.
`StrEnum` members are strings, so they can be passed directly:

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
| `jq_filter` | Extract text from JSONL using the `jq_expression`. orchcore evaluates the expression itself, so no `jq` binary is required. |
| `direct_file` | Agent writes output to a file; orchcore reads it. |
| `stdout_capture` | Capture the full stdout as output. |

### Environment Policy

Agent subprocesses default to `env_policy = "filtered"`. This keeps basic process variables such as `PATH` and `HOME`, but removes common credential and agent configuration families such as `ANTHROPIC_*`, `OPENAI_*`, `GITHUB_*`, `AWS_*`, proxy variables, and telemetry (`OTEL_*`) variables. Values in `env_vars` are always applied last.

When an agent needs a variable from the parent environment, use one of the following:

```toml
# Keep the old full-inheritance behavior for this agent.
env_policy = "inherit"

# Or keep the filtered default and re-admit specific names.
env_passlist = ["ANTHROPIC_API_KEY"]

# Or pass explicit literal values from your own config layer.
[agents.claude.env_vars]
ANTHROPIC_API_KEY = "..."
```

`env_policy = "clean"` starts from a minimal platform environment. It helps with reproducibility checks, but it keeps `HOME`, so CLIs still read their configuration from the home directory.

### Version Compatibility

Agent CLIs release often, and without a version check a stream-format change in a new release shows up only as parse warnings. Version expectations are declared as registry data:

```toml
[agents.claude]
# ...
version_command = ["--version"]            # default; [] disables checking
compatible_versions = [">=2.1.112,<3"]

[[agents.claude.incompatible_versions]]
spec = "<=2.0.0"
reason = "stream-json v1 format; https://github.com/anthropics/claude-code/issues/NNN"
```

A specifier is a comma-separated list of `==`, `!=`, `>=`, `<=`, `>`, and `<` clauses that must all match. `==` and `!=` accept a trailing `.*` wildcard (`"==2.1.*"`). A list matches if any of its entries matches. A version that matches a known-incompatible range is treated as incompatible even if it also matches a compatible one.

At runtime, the check behaves as follows:

- It runs once per binary path per process, and the result is cached, including failures. The check is advisory: it never fails the run, and a hard 10-second timeout bounds the delay.
- The version subprocess runs under the same rules as the agent run: the environment filtered by the agent's `env_policy`, the run's working directory, and no stdin.
- Known-compatible versions log at DEBUG, known-incompatible versions at WARNING (with the recorded reason), versions outside the declared ranges at INFO, and agents without declared ranges at DEBUG.
- The detected version is stored on `AgentResult.agent_version` (`None` when detection is disabled or fails).

Each `incompatible_versions` entry should carry a `reason` that links to the upstream issue or changelog entry behind it, so the ranges can be reviewed when the CLI changes.

### Prompt Transport (`prompt_via`)

By default, the prompt is passed as a command-line argument (`prompt_via = "argv"`). A very large prompt can exceed `ARG_MAX` (POSIX) or the 32K-character `CreateProcess` limit (Windows), and argv is visible in local process listings. With `prompt_via = "stdin"`, the prompt is left out of argv. orchcore opens a stdin pipe, writes the encoded prompt while it reads the output stream (so a full pipe buffer cannot deadlock the two), and then closes stdin.

Per-CLI stdin support:

| CLI | Configuration | Notes |
|-----|---------------|-------|
| Claude Code | `subcommand = "-p"`, `prompt_via = "stdin"` | `-p` accepts the prompt from piped stdin; capped at 10 MB as of v2.1.128; for larger contexts, reference files from the prompt. |
| Codex CLI | `subcommand = "exec"`, `prompt_via = "stdin"`, `stdin_sentinel = "-"` | `codex exec -` reads the full prompt from stdin; the `-` placeholder must be the prompt argument. |
| Antigravity CLI | `subcommand = "-p"`, keep `prompt_via = "argv"` | `-p` takes the prompt as its value and does not read stdin (`-p -` is sent as a literal `-`). |
| Others | keep `prompt_via = "argv"` | Use argv until stdin support is verified for the specific CLI. |

orchcore drives non-interactive CLI modes only. stdin is closed after the prompt is written, so CLIs that expect stdin to stay open for interaction are not supported.

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

[agents.antigravity]
binary = "agy"
model = "gemini-3.8-flash-high"  # `agy models` lists the slugs your plan offers
subcommand = "-p"
stream_format = "antigravity"
# Keep the CLI from updating itself between pipeline runs.
env_vars = { AGY_CLI_DISABLE_AUTO_UPDATE = "true" }

# A ToolSet adds `--output-format stream-json` and maps its permission to
# `--mode accept-edits` (workspace-write) or `--dangerously-skip-permissions`
# (full-access). Without a ToolSet, put `--output-format stream-json` here.
[agents.antigravity.flags]
plan = ["--effort", "high"]
fix = []

[agents.antigravity.output_extraction]
strategy = "jq_filter"
```

Antigravity runs headless with `agy -p <prompt>`. In its default mode it asks
before edits and commands; a headless run cannot ask, so it denies those tools
and still exits 0 with `status: SUCCESS`. orchcore marks each denied tool step
`tool_status="error"` and logs the result's `denied_actions` at INFO. Tool
allowlists live in the CLI's `settings.json` (`permissions.allow`), so
`ToolSet.internal` is not translated into flags.

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

Loading is atomic: every entry is parsed and validated before any is
registered. With the default `on_error="raise"`, a file with invalid entries
raises one `ValueError` that names all of them, and the registry is left
unchanged. Pass `on_error="skip"` to register the valid entries and log a
warning for each invalid one (the pre-1.0 behavior for non-table entries).

TOML values are used literally. orchcore does not expand `${VAR}` references,
so a value like `"${ANTHROPIC_API_KEY}"` reaches the subprocess as that exact
string. To pass credentials from the parent environment, use `env_policy` or
`env_passlist` (see [Environment Policy](#environment-policy)), or resolve the
values in your own configuration layer before registering.

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
registry.list_agents()  # ["claude", "codex", "antigravity"]

# List agents whose binary is on PATH
registry.available()  # ["claude", "codex"]  (agy not installed)

# Validate a list of agent names
missing = registry.validate(["claude", "codex", "unknown"])
# Returns ["unknown"] — agents not registered or not on PATH
```

## Phase-Level Tool Configuration (ToolSet)

Tools are assigned per phase, not per agent ([ADR-009](../architecture/adrs/009-tool-assignment-as-phase-level-concern.md)), so the same agent can be read-only in a planning phase and write-enabled in an execution phase.

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
| `internal` | `tuple[str, ...]` | `()` | Agent-native tools (e.g., Read, Write, Edit, Bash); lists are accepted |
| `mcp` | `tuple[str, ...]` | `()` | MCP server tools (e.g., Exa, Tavily); lists are accepted |
| `permission` | `str` | `"read-only"` | Write access level: `read-only`, `workspace-write`, `full-access` |
| `max_turns` | `int` | `25` | Maximum conversation turns for the agent |

### Tool Resolution Order

For each agent in a phase, the effective ToolSet is the first one found in this order (from highest to lowest priority):

```
Phase.agent_tools[agent]  >  explicit toolset arg  >  Phase.tools  >  none
```

With `Phase.agent_tools`, agents in the same phase can have different ToolSets.

Flag profiles are independent of this resolution: a selected profile's flags
are always applied, and the ToolSet translation (when one resolves) is
appended after them.

## Adding a New Agent CLI

To add support for a new agent CLI:

1. Check whether its JSONL output matches one of the five supported stream formats. If it does, use that format.
2. Add a TOML entry with the agent's binary, model, subcommand, stream format, flag profiles, and output extraction strategy.
3. Run the agent through a one-phase pipeline and check that its tool calls, text, and result appear as `StreamEvent`s.

If the output matches none of the five formats, orchcore needs a new `StreamFormat` value and a parser module in `orchcore/stream/formats/`, which is a code change.

## Related

- [Configuration Reference](../reference/configuration.md): per-agent overrides in settings
- [Stream Events Reference](../reference/stream-events.md): the `StreamFormat` enum
- [ADR-007: Registry pattern for agent management](../architecture/adrs/007-registry-pattern-for-agent-management.md)
- [ADR-009: Tool assignment as phase-level concern](../architecture/adrs/009-tool-assignment-as-phase-level-concern.md)
