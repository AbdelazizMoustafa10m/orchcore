# Configuration Reference

orchcore uses a layered configuration system built on [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/). Configuration is resolved from multiple sources with a defined priority order.

## Resolution Order

Sources are listed from highest priority (wins) to lowest:

| Priority | Source | Example |
|----------|--------|---------|
| 1 | Constructor kwargs / CLI overrides | `OrchcoreSettings(concurrency=8)` |
| 2 | Environment variables | `ORCHCORE_CONCURRENCY=8` |
| 3 | `.env` file | `ORCHCORE_CONCURRENCY=8` in `.env` |
| 4 | Profile overlay (if active) | `[profiles.fast]` section in TOML |
| 5 | Project TOML | `orchcore.toml` in working directory |
| 6 | User TOML | `~/.config/orchcore/config.toml` |
| 7 | pyproject.toml | `[tool.orchcore]` section |
| 8 | Built-in defaults | Defined on `OrchcoreSettings` fields |

## Settings Fields

### `OrchcoreSettings`

| Field | Type | Default | Env Var | Description |
|-------|------|---------|---------|-------------|
| `concurrency` | `int` | `4` | `ORCHCORE_CONCURRENCY` | Maximum number of concurrent agent subprocesses |
| `stall_timeout` | `int` | `300` | `ORCHCORE_STALL_TIMEOUT` | Seconds of inactivity before stall detection triggers |
| `deep_tool_timeout` | `int` | `600` | `ORCHCORE_DEEP_TOOL_TIMEOUT` | Timeout for long-running tools (Exa, Tavily, etc.) |
| `workspace_dir` | `str` | `.orchcore-workspace` | `ORCHCORE_WORKSPACE_DIR` | Root directory for workspace artifacts |
| `reports_dir` | `str` | `reports` | `ORCHCORE_REPORTS_DIR` | Directory for generated reports |
| `max_retries` | `int` | `3` | `ORCHCORE_MAX_RETRIES` | Maximum retry attempts per agent on failure |
| `max_wait` | `int` | `21600` | `ORCHCORE_MAX_WAIT` | Maximum wait time in seconds for rate-limit recovery (6 hours) |
| `log_level` | `str` | `info` | `ORCHCORE_LOG_LEVEL` | Logging verbosity: `debug`, `info`, `warn`, `error` |
| `profile` | `str \| None` | `None` | `ORCHCORE_PROFILE` | Named configuration profile to activate |
| `agents` | `dict` | `{}` | — | Per-agent overrides (see below) |

## TOML Configuration Files

### Project Configuration (`orchcore.toml`)

```toml
concurrency = 4
stall_timeout = 300
deep_tool_timeout = 600
max_retries = 3
log_level = "info"

[agents.claude]
stall_timeout = 600
model = "claude-sonnet-4-20250514"

[agents.codex]
stall_timeout = 120
```

### pyproject.toml

```toml
[tool.orchcore]
concurrency = 4
stall_timeout = 300

[tool.orchcore.agents.claude]
model = "claude-sonnet-4-20250514"
```

### User Configuration (`~/.config/orchcore/config.toml`)

Global defaults that apply across all projects:

```toml
log_level = "info"
max_retries = 3
```

## Profiles

Profiles are named configuration overlays. They sit between `.env` and base TOML files in the priority chain. When a profile is active, its values override base TOML defaults but are still overridden by env vars and constructor kwargs.

### Defining Profiles

```toml
# orchcore.toml

[profiles.fast]
max_retries = 1
stall_timeout = 60
deep_tool_timeout = 120

[profiles.deep]
stall_timeout = 900
deep_tool_timeout = 1800
max_retries = 5

[profiles.ci]
max_retries = 0
log_level = "error"
```

### Activating a Profile

Profiles can be activated via any configuration source:

```bash
# Environment variable
export ORCHCORE_PROFILE=fast

# .env file
ORCHCORE_PROFILE=fast

# Constructor kwarg
settings = OrchcoreSettings(profile="fast")
```

Or use the helper function for explicit profile loading:

```python
from orchcore.config import load_settings_with_profile

settings = load_settings_with_profile(profile="fast")
```

## Environment Variables

All settings fields are available as environment variables with the `ORCHCORE_` prefix:

```bash
export ORCHCORE_CONCURRENCY=8
export ORCHCORE_STALL_TIMEOUT=600
export ORCHCORE_MAX_RETRIES=5
export ORCHCORE_LOG_LEVEL=debug
export ORCHCORE_PROFILE=deep
```

## Extending Settings

Consuming projects can subclass `OrchcoreSettings` to add domain-specific fields:

```python
from orchcore.config import OrchcoreSettings

class MyProjectSettings(OrchcoreSettings):
    review_threshold: float = 0.8
    auto_merge: bool = False
```

The subclass inherits the full layered resolution chain, and its custom fields are also resolved from TOML, env vars, etc.

## Per-Agent Overrides

The `agents` dict in settings allows per-agent customization:

```toml
[agents.claude]
stall_timeout = 600
deep_tool_timeout = 1200

[agents.codex]
stall_timeout = 120
```

These overrides are merged with the agent's base `AgentConfig` at runtime.

## Related

- [ADR-005: Multi-source layered configuration](../architecture/adrs/005-multi-source-layered-configuration.md)
- [Quick Start](../getting-started/quickstart.md)
