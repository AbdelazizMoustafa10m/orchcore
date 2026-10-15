# orchcore

orchcore is a typed, async-first Python 3.12+ library for running AI coding agent CLIs in phase-based pipelines. It launches agent CLIs as subprocesses, normalizes their JSONL output through one stream pipeline, runs phases in dependency order, and handles configuration, rate-limit recovery, and shutdown. Consuming projects supply the domain logic: phase definitions, prompt templates, agent configuration, and presentation.

The library was extracted from four production orchestration systems, where an estimated 60-70% of the orchestration code was the same infrastructure.

## Features

- Launches agent CLIs as async subprocesses, captures their output streams, and limits how many run at once.
- Normalizes five JSONL output formats into one `StreamEvent` model through a four-stage stream pipeline (filter, parse, monitor, stall detection).
- Runs phases sequentially or in parallel, in dependency order, with resume, skip, and single-phase options.
- Detects rate limits, parses reset times (including named time zones), and retries after a backoff wait.
- Layers settings from TOML files, `.env` and environment variables, constructor arguments, and named profiles, built on pydantic-settings.
- Separates the engine from presentation through the `UICallback` protocol, so the consuming project chooses Rich, Textual, or headless output.
- Defines agents as data: a new agent CLI that uses a supported stream format needs only a TOML entry.
- On SIGINT or SIGTERM, terminates agent process trees and force-kills them after a 30-second grace period. With a workspace attached, completed phases are saved for resume.
- Starts agent subprocesses with a filtered environment by default and an explicit working directory. Git recovery runs only when a phase opts in.
- Emits optional OpenTelemetry traces through `OrchcoreTelemetry`.

## Where to Start

- [Installation](getting-started/installation.md): requirements, install options, and optional extras.
- [Quick Start](getting-started/quickstart.md): define agents, build phases, and run a pipeline.
- [Configuration Reference](reference/configuration.md): settings fields, sources, profiles, and environment variables.
- [Stream Events Reference](reference/stream-events.md): `StreamEvent` fields, event types, and agent states.
- [UICallback Reference](reference/ui-callback.md): protocol methods and the built-in implementations.
- [Architecture Overview](architecture/overview.md): package layout, core abstractions, and design decisions.
- [Stream Pipeline](architecture/stream-pipeline.md): how the four stream-processing stages work.
- [Design Document](architecture/design.md): problem statement, requirements, and key interfaces.

## Modules

| Module | Purpose |
|--------|---------|
| `stream/` | 4-stage pipeline (Filter → Parse → Monitor → Stall Detect) for 5 agent formats |
| `pipeline/` | Phase orchestration: sequential or parallel multi-agent execution in topological dependency order |
| `runner/` | Async subprocess management with stdout/stderr streaming and optional stdin prompt transport (`prompt_via = "stdin"`) |
| `registry/` | Agent configurations as data (TOML/dict) with runtime lookup |
| `config/` | Layered settings from TOML files, profiles, `.env`, environment variables, and constructor arguments |
| `recovery/` | Rate-limit detection, exponential backoff, git dirty-tree recovery |
| `workspace/` | Artifact lifecycle management |
| `prompt/` | Jinja2 template rendering with frontmatter stripping |
| `ui/` | `UICallback` protocol, implemented by each consuming project for its own display layer |
| `signals/` | Graceful SIGINT/SIGTERM shutdown |
| `display/` | Colored stderr logging (no Rich dependency in core) |
| `observability/` | Optional OpenTelemetry integration |

## Guides

| Guide | Description |
|-------|-------------|
| [Writing a UICallback](guides/writing-a-uicallback.md) | Implement a display layer for your project |
| [Agent Registry](guides/agent-registry.md) | Configure agents in TOML and add support for a new agent CLI |
| [Recovery & Retry](guides/recovery-and-retry.md) | Rate limits, backoff, git recovery, failure modes |
| [Flow Control](guides/flow-control.md) | Pause, resume, and skip phases of a running pipeline |
| [Workspace Management](guides/workspace.md) | Artifact lifecycle, archival, and cleanup |
| [Prompt Templating](guides/prompt-templating.md) | Jinja2 templates, frontmatter stripping, template loading |
| [Signal Handling](guides/signal-handling.md) | Cooperative SIGINT/SIGTERM shutdown flag |
| [Observability](guides/observability.md) | Optional OpenTelemetry tracing |
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
| [011](architecture/adrs/011-consumer-defined-flag-profiles.md) | Consumer-defined flag profiles replace the `AgentMode` enum |
