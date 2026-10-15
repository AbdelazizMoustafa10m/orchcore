# Configuration Reference

`OrchcoreSettings` is a [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) model that reads each field from several sources in a fixed order of precedence. orchcore's runners do not read it themselves: the consuming project loads the settings and passes the values to the components it constructs, e.g., `PhaseRunner(runner, registry, max_concurrency=settings.concurrency)` or `registry.with_overrides(settings.agents)`.

File paths are relative to the current working directory, except for the user configuration file in `~/.config/orchcore/`.

## Resolution Order

The order of precedence is as follows (from highest to lowest):

| Priority | Source | Example |
|----------|--------|---------|
| 1 | Constructor kwargs / CLI overrides | `OrchcoreSettings(concurrency=8)` |
| 2 | Environment variables | `ORCHCORE_CONCURRENCY=8` |
| 3 | `.env` file | `ORCHCORE_CONCURRENCY=8` in `.env` |
| 4 | Profile overlay (only through `load_settings_with_profile()`) | `[profiles.fast]` section in TOML |
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
| `deep_tool_timeout` | `int` | `600` | `ORCHCORE_DEEP_TOOL_TIMEOUT` | Stall timeout in seconds while a long-running deep tool (e.g., Exa or Tavily search) is active |
| `workspace_dir` | `str` | `.orchcore-workspace` | `ORCHCORE_WORKSPACE_DIR` | Root directory for workspace artifacts |
| `reports_dir` | `str` | `reports` | `ORCHCORE_REPORTS_DIR` | Directory for generated reports |
| `max_retries` | `int` | `3` | `ORCHCORE_MAX_RETRIES` | Maximum retry attempts per agent (the engine retries rate-limit failures only) |
| `max_wait` | `int` | `21600` | `ORCHCORE_MAX_WAIT` | Upper bound in seconds for a single rate-limit wait (6 hours) |
| `log_level` | `str` | `info` | `ORCHCORE_LOG_LEVEL` | Logging verbosity: `debug`, `info`, `warn`, `error` |
| `profile` | `str \| None` | `None` | `ORCHCORE_PROFILE` | Named configuration profile to activate |
| `agents` | `dict` | `{}` | n/a | Per-agent overrides (see [Per-Agent Overrides](#per-agent-overrides)) |

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
max_runtime = 1800
kill_on_stall = true
env_policy = "filtered"
env_passlist = ["ANTHROPIC_API_KEY"]
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

Defaults for every project run by the current user:

```toml
log_level = "info"
max_retries = 3
```

## Profiles

A **profile** is a named overlay of settings. When a profile is active, its values override the base TOML files but are still overridden by `.env` values, environment variables, and constructor kwargs.

Only `load_settings_with_profile()` applies a profile. Constructing `OrchcoreSettings` directly stores the `profile` name but leaves the overlay out, so `OrchcoreSettings(profile="fast").stall_timeout` still returns the base value.

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

Profile tables are read from `[profiles.<name>]` in `orchcore.toml` and the user configuration file, and from `[tool.orchcore.profiles.<name>]` in `pyproject.toml`.

`load_settings_with_profile()` resolves settings in two passes. The first pass reads the normal sources to find the profile name; the second pass applies the matching profile tables. The name is taken from the `profile` argument if given, otherwise from the `profile` setting (`ORCHCORE_PROFILE`, a `.env` entry, or a `profile=` keyword override).

```python
from orchcore.config import load_settings_with_profile

# Name the profile explicitly
settings = load_settings_with_profile(profile="fast")

# Or take the name from ORCHCORE_PROFILE / .env
settings = load_settings_with_profile()
```

```bash
export ORCHCORE_PROFILE=fast
```

If no profile name is found, `load_settings_with_profile()` returns the same result as `OrchcoreSettings()`. A profile name with no matching table applies no overlay.

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

Consuming projects can subclass `OrchcoreSettings` to add their own fields:

```python
from orchcore.config import OrchcoreSettings

class MyProjectSettings(OrchcoreSettings):
    review_threshold: float = 0.8
    auto_merge: bool = False
```

The subclass keeps the same sources and order of precedence, so its fields are also read from TOML files, `.env`, and `ORCHCORE_*` environment variables. Pass the subclass to `load_settings_with_profile(MyProjectSettings)` to apply profiles to it.

## Per-Agent Overrides

The `agents` table holds per-agent field overrides, keyed by agent name:

```toml
[agents.claude]
stall_timeout = 600
deep_tool_timeout = 1200
max_runtime = 1800
kill_on_stall = true
env_policy = "filtered"
env_passlist = ["ANTHROPIC_API_KEY"]

[agents.codex]
stall_timeout = 120
```

orchcore does not apply these overrides automatically. Pass them to `AgentRegistry.with_overrides()`, which returns a new registry with each override merged into the registered `AgentConfig`. `env_vars` entries are merged per key with the agent's existing `env_vars`:

```python
settings = load_settings_with_profile()
registry = registry.with_overrides(settings.agents)
```

## Related

- [ADR-005: Multi-source layered configuration](../architecture/adrs/005-multi-source-layered-configuration.md)
- [Quick Start](../getting-started/quickstart.md)
