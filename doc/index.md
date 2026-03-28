# orchcore

**Reusable orchestration core for AI coding agent CLI pipelines.**

---

orchcore extracts the 60-70% of duplicated infrastructure from production AI orchestration systems into a single, typed, async-first Python 3.12+ library. It handles launching agent CLIs as subprocesses, processing their JSONL output through a unified stream pipeline, orchestrating multi-phase DAG-based execution, and managing configuration, recovery, and graceful shutdown — so consuming projects only implement domain-specific logic.

## Features

- **Multi-agent subprocess orchestration** — launch and manage Claude, Codex, Gemini, Copilot, and OpenCode CLIs as async subprocesses
- **Unified stream processing** — 4-stage pipeline (Filter, Parse, Monitor, Stall Detect) normalizes 5 different JSONL formats into a single `StreamEvent` model
- **DAG-based phase pipelines** — sequential and parallel phase execution with dependency ordering, partial failure semantics, and resume support
- **Rate-limit recovery** — automatic detection with timezone-aware reset parsing and exponential backoff
- **Layered configuration** — TOML files, environment variables, CLI overrides, and named profiles via pydantic-settings
- **Protocol-based UI** — `UICallback` protocol decouples engine from presentation; plug in Rich, Textual, or headless output
- **Registry-as-data** — add new agent support via TOML configuration alone, zero code changes
- **Graceful shutdown** — SIGINT/SIGTERM handling with async task cancellation and subprocess cleanup
- **Git dirty-tree recovery** — auto-stash or auto-commit before retry to ensure clean working state
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
| `stream/` | 4-stage pipeline normalizing JSONL from 5 agent formats into unified `StreamEvent` models |
| `pipeline/` | DAG-based phase orchestration engine for sequential/parallel multi-agent execution |
| `runner/` | Async subprocess management — launches agent CLIs with stdin/stdout/stderr piping |
| `registry/` | Agent configurations as data (TOML/dict) with runtime lookup and tool resolution |
| `config/` | Layered configuration: TOML → env vars (`ORCHCORE_*`) → CLI overrides → profiles |
| `recovery/` | Rate-limit detection, retry with exponential backoff, git dirty-tree recovery |
| `workspace/` | Manages `.orchcore-workspace/outputs/<phase>/<agent>.md` artifact lifecycle |
| `prompt/` | Jinja2 template rendering with frontmatter stripping |
| `ui/` | `UICallback` protocol — consuming projects implement their own display layer |
| `signals/` | Graceful SIGINT/SIGTERM shutdown with async task cancellation |
| `display/` | Colored stderr logging via ANSI codes (no Rich dependency in core) |
| `observability/` | Optional OpenTelemetry integration |

## Guides

| Guide | Description |
|-------|-------------|
| [Writing a UICallback](guides/writing-a-uicallback.md) | Implement custom display layers for your project |
| [Agent Registry](guides/agent-registry.md) | Configure agents via TOML, add new agent support |
| [Recovery & Retry](guides/recovery-and-retry.md) | Rate limits, backoff, git recovery, failure modes |
| [Workspace Management](guides/workspace.md) | Artifact lifecycle, archival, and cleanup |
| [Prompt Templating](guides/prompt-templating.md) | Jinja2 templates, frontmatter stripping, template loading |
| [Signal Handling](guides/signal-handling.md) | Graceful SIGINT/SIGTERM shutdown and task cancellation |
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
