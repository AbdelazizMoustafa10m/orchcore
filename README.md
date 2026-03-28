# orchcore

> Reusable orchestration core for AI coding agent CLI pipelines.

[![CI](https://github.com/AbdelazizMoustafa10m/orchcore/actions/workflows/ci.yml/badge.svg)](https://github.com/AbdelazizMoustafa10m/orchcore/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/AbdelazizMoustafa10m/orchcore)](https://github.com/AbdelazizMoustafa10m/orchcore/releases)
[![PyPI](https://img.shields.io/pypi/v/orchcore)](https://pypi.org/project/orchcore/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![mypy: strict](https://img.shields.io/badge/mypy-strict-blue.svg)](https://mypy-lang.org/)
[![Ruff](https://img.shields.io/badge/linter-ruff-orange.svg)](https://docs.astral.sh/ruff/)
[![Coverage: 90%+](https://img.shields.io/badge/coverage-90%25%2B-brightgreen.svg)]()

## What is orchcore?

orchcore is an async-first Python 3.12+ library that provides unified infrastructure for launching, monitoring, and managing multiple AI coding agent CLIs (Claude, Codex, Gemini, Copilot, OpenCode) as subprocesses through phase-based pipelines. It was extracted from four production orchestration systems — Planora, Articles, Finvault, and Raven — eliminating 60-70% of duplicated infrastructure so consuming projects only implement domain-specific logic.

## Features

- **Multi-agent subprocess orchestration** — async launch, stream capture, concurrency control
- **Unified stream processing** — 4-stage pipeline normalizes 5 JSONL formats into a single `StreamEvent` model
- **DAG-based phase pipelines** — sequential/parallel execution with dependency ordering and resume
- **Rate-limit recovery** — automatic detection, timezone-aware reset parsing, exponential backoff
- **Layered configuration** — TOML files, env vars, CLI overrides, named profiles
- **Protocol-based UI** — `UICallback` decouples engine from display (Rich, Textual, headless)
- **Registry-as-data** — new agent support via TOML config alone, zero code changes
- **Graceful shutdown** — SIGINT/SIGTERM with subprocess cleanup and state preservation

## Installation

```bash
uv pip install orchcore
```

From source:

```bash
git clone https://github.com/AbdelazizMoustafa10m/orchcore.git
cd orchcore
uv pip install -e ".[dev]"
```

**Requirements:** Python 3.12+

## Quick Start

### 1. Define Agents

```toml
# agents.toml
[agents.claude]
binary = "claude"
model = "claude-sonnet-4-20250514"
subcommand = "-p"
stream_format = "claude"

[agents.claude.flags]
plan = ["--think", "--verbose"]

[agents.claude.output_extraction]
strategy = "jq_filter"
jq_expression = ".content[0].text"
```

### 2. Run a Pipeline

```python
import asyncio
from pathlib import Path
from orchcore.pipeline import PipelineRunner, PhaseRunner, Phase
from orchcore.registry import AgentRegistry, AgentMode, ToolSet
from orchcore.runner import AgentRunner
from orchcore.ui import NullCallback

async def main() -> None:
    registry = AgentRegistry()
    registry.load_from_toml(Path("agents.toml"))

    phase = Phase(
        name="planning",
        agents=["claude"],
        tools=ToolSet(internal=["Read", "Glob", "Grep"], permission="read-only"),
    )

    runner = AgentRunner()
    phase_runner = PhaseRunner(runner, registry, max_concurrency=4)
    pipeline = PipelineRunner(phase_runner)

    result = await pipeline.run_pipeline(
        phases=[phase],
        prompts={"planning": "Analyze the codebase and create a plan."},
        ui_callback=NullCallback(),
        mode=AgentMode.PLAN,
    )
    print(f"Success: {result.success} | Cost: ${result.total_cost_usd}")

asyncio.run(main())
```

## Modules

| Module | Purpose |
|--------|---------|
| `stream/` | 4-stage pipeline (Filter → Parse → Monitor → Stall Detect) for 5 agent formats |
| `pipeline/` | DAG-based phase orchestration — sequential/parallel multi-agent execution |
| `runner/` | Async subprocess management with stdin/stdout/stderr piping |
| `registry/` | Agent configurations as data (TOML/dict) with runtime lookup |
| `config/` | Layered configuration: TOML → env vars → CLI overrides → profiles |
| `recovery/` | Rate-limit detection, exponential backoff, git dirty-tree recovery |
| `workspace/` | Artifact lifecycle management |
| `prompt/` | Jinja2 template rendering with frontmatter stripping |
| `ui/` | `UICallback` protocol for pluggable display layers |
| `signals/` | Graceful SIGINT/SIGTERM shutdown |
| `display/` | Colored stderr logging (no Rich dependency in core) |
| `observability/` | Optional OpenTelemetry integration |

## Documentation

Full documentation is available at **[abdelazizmoustafa10m.github.io/orchcore](https://abdelazizmoustafa10m.github.io/orchcore/)**.

| Document | Description |
|----------|-------------|
| [Installation](doc/getting-started/installation.md) | Prerequisites, install options, extras |
| [Quick Start](doc/getting-started/quickstart.md) | Define agents, build phases, run pipelines |
| [Configuration Reference](doc/reference/configuration.md) | Full settings table, profiles, env vars |
| [Stream Events Reference](doc/reference/stream-events.md) | StreamEvent fields, types, agent states |
| [UICallback Reference](doc/reference/ui-callback.md) | Protocol methods and built-in implementations |
| [Architecture](doc/architecture/overview.md) | Package layout, core abstractions, design decisions |
| [Stream Pipeline](doc/architecture/stream-pipeline.md) | 4-stage composable pipeline deep-dive |
| [Design Document](doc/architecture/design.md) | Problem statement, requirements, proposed design |
| [Writing a UICallback](doc/guides/writing-a-uicallback.md) | Build custom display layers |
| [Agent Registry](doc/guides/agent-registry.md) | TOML config, adding new agents, ToolSets |
| [Recovery & Retry](doc/guides/recovery-and-retry.md) | Rate limits, backoff, failure modes |
| [Contributing](doc/development/contributing.md) | Dev setup, code standards, testing |

## Contributing

See [CONTRIBUTING](doc/development/contributing.md) for development setup, testing instructions, and code standards.

## License

orchcore is released under the [MIT License](LICENSE).
