# orchcore

**Reusable orchestration core for AI coding agent CLI pipelines.**

---

orchcore extracts the 60-70% of duplicated infrastructure from production AI orchestration systems into a single, typed, async-first Python 3.12+ library. It handles launching agent CLIs as subprocesses, processing their JSONL output through a unified stream pipeline, orchestrating multi-phase execution with dependency checks, and managing configuration, recovery, and graceful shutdown — so consuming projects only implement domain-specific logic.

## Features

- **Multi-agent subprocess orchestration** — async launch, stream capture, concurrency control
- **Unified stream processing** — 4-stage pipeline normalizes 5 JSONL formats into a single `StreamEvent` model
- **Phase pipelines** — sequential/parallel execution with dependency checks and resume
- **Rate-limit recovery** — automatic detection with timezone-aware reset parsing and exponential backoff
- **Layered configuration** — TOML files, environment variables, CLI overrides, and named profiles via pydantic-settings
- **Protocol-based UI** — `UICallback` protocol decouples engine from presentation; plug in Rich, Textual, or headless output
- **Registry-as-data** — add new agent support via TOML configuration alone, zero code changes
- **Graceful shutdown** — SIGINT/SIGTERM with subprocess cleanup and state preservation
- **Safe subprocess boundaries** — filtered agent environments by default, explicit cwd support, and opt-in git recovery
- **Optional observability** — OpenTelemetry integration via `OrchcoreTelemetry`

## Quick Links

- :material-download: **[Installation](getting-started/installation.md)** — Get orchcore installed
- :material-rocket-launch: **[Quick Start](getting-started/quickstart.md)** — Define agents, build phases, run a pipeline
- :material-cog: **[Configuration Reference](reference/configuration.md)** — Full settings table, profiles, env vars
- :material-format-list-bulleted: **[Stream Events Reference](reference/stream-events.md)** — StreamEvent fields, types, and states
- :material-puzzle: **[UICallback Reference](reference/ui-callback.md)** — Protocol methods and built-in implementations
- :material-sitemap: **[Architecture Overview](architecture/overview.md)** — Package layout, core abstractions, design decisions
- :material-pipe: **[Stream Pipeline](architecture/stream-pipeline.md)** — 4-stage composable stream processing deep-dive
- :material-book-open: **[Design Document](architecture/design.md)** — Problem statement, requirements, proposed design

## Modules at a Glance

| Module | Purpose |
|--------|---------|
| `stream/` | 4-stage pipeline (Filter → Parse → Monitor → Stall Detect) for 5 agent formats |
| `pipeline/` | Phase orchestration — sequential/parallel multi-agent execution in topological dependency order |
| `runner/` | Async subprocess management with stdout/stderr streaming and optional stdin prompt transport (`prompt_via = "stdin"`) |
| `registry/` | Agent configurations as data (TOML/dict) with runtime lookup |
| `config/` | Layered configuration: TOML files, env vars, CLI overrides, and profiles |
| `recovery/` | Rate-limit detection, exponential backoff, git dirty-tree recovery |
| `workspace/` | Artifact lifecycle management |
| `prompt/` | Jinja2 template rendering with frontmatter stripping |
| `ui/` | `UICallback` protocol — consuming projects implement their own display layer |
| `signals/` | Graceful SIGINT/SIGTERM shutdown |
| `display/` | Colored stderr logging (no Rich dependency in core) |
| `observability/` | Optional OpenTelemetry integration |

## Guides

| Guide | Description |
|-------|-------------|
| [Writing a UICallback](guides/writing-a-uicallback.md) | Implement custom display layers for your project |
| [Agent Registry](guides/agent-registry.md) | Configure agents via TOML, add new agent support |
| [Recovery & Retry](guides/recovery-and-retry.md) | Rate limits, backoff, git recovery, failure modes |
| [Workspace Management](guides/workspace.md) | Artifact lifecycle, archival, and cleanup |
| [Prompt Templating](guides/prompt-templating.md) | Jinja2 templates, frontmatter stripping, template loading |
| [Signal Handling](guides/signal-handling.md) | Cooperative SIGINT/SIGTERM shutdown flag and graceful exit |
| [Observability](guides/observability.md) | Optional OpenTelemetry tracing integration |
| [Display Utilities](guides/display.md) | ANSI colored logging and formatting helpers |

## Architecture Decision Records

| ADR | Decision |
|-----|----------|
| [001](architecture/adrs/001-extract-reusable-orchestration-core.md) | Extract reusable orchestration core from 4 production systems |
| [002](architecture/adrs/002-async-first-architecture-with-asyncio.md) | Async-first with stdlib asyncio |
| [003](architecture/adrs/003-protocol-based-ui-decoupling.md) | Protocol-based UI decoupling |
| [004](architecture/adrs/004-composable-stream-processing-pipeline.md) | Composable 4-stage stream processing pipeline |
| [005](architecture/adrs/005-multi-source-layered-configuration.md) | Multi-source layered configuration |
| [006](architecture/adrs/006-pydantic-for-all-data-models.md) | Pydantic for all data models |
| [007](architecture/adrs/007-registry-pattern-for-agent-management.md) | Registry pattern for agent management |
| [008](architecture/adrs/008-partial-failure-semantics-with-retry.md) | Partial failure semantics with retry |
| [009](architecture/adrs/009-tool-assignment-as-phase-level-concern.md) | Tool assignment as phase-level concern |
| [010](architecture/adrs/010-topological-phase-ordering-and-success-semantics.md) | Topological phase ordering and explicit success semantics |
