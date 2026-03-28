# Quick Start

This guide walks you through defining agents, building phases, and running your first pipeline with orchcore.

## 1. Define Your Agents (TOML)

Create an `agents.toml` file in your project root:

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

[agents.claude.env_vars]
ANTHROPIC_API_KEY = "${ANTHROPIC_API_KEY}"

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
```

Each `[agents.<name>]` section defines an agent's binary path, model, CLI flags per mode, stream format for output parsing, and how to extract final output.

See the [Agent Registry guide](../guides/agent-registry.md) for all configuration options.

## 2. Define Execution Phases

Phases are the building blocks of a pipeline. Each phase specifies which agents to run, whether to run them sequentially or in parallel, and what tools they can access.

```python
from orchcore.pipeline import Phase
from orchcore.registry import ToolSet

planning = Phase(
    name="planning",
    agents=["claude"],
    parallel=False,
    tools=ToolSet(
        internal=["Read", "Glob", "Grep"],
        mcp=[],
        permission="read-only",
        max_turns=15,
    ),
)

execution = Phase(
    name="execution",
    agents=["claude", "codex"],
    parallel=True,
    depends_on=["planning"],
    tools=ToolSet(
        internal=["Read", "Write", "Edit", "Bash"],
        mcp=[],
        permission="workspace-write",
        max_turns=25,
    ),
)
```

## 3. Run the Pipeline

Wire up the registry, runner, and pipeline engine:

```python
import asyncio
from pathlib import Path
from orchcore.pipeline import PipelineRunner, PhaseRunner
from orchcore.registry import AgentRegistry, AgentMode
from orchcore.runner import AgentRunner
from orchcore.ui import NullCallback

async def main() -> None:
    # Load agent configs from TOML
    registry = AgentRegistry()
    registry.load_from_toml(Path("agents.toml"))

    # Wire up the execution stack
    runner = AgentRunner()
    phase_runner = PhaseRunner(runner, registry, max_concurrency=4)
    pipeline = PipelineRunner(phase_runner)

    # Run the pipeline
    result = await pipeline.run_pipeline(
        phases=[planning, execution],
        prompts={
            "planning": "Analyze the codebase and create an implementation plan.",
            "execution": "Implement the changes from the planning phase.",
        },
        ui_callback=NullCallback(),
        mode=AgentMode.PLAN,
    )

    print(f"Success: {result.success}")
    print(f"Duration: {result.total_duration}")
    print(f"Cost: ${result.total_cost_usd}")

    for phase_result in result.phases:
        print(f"  {phase_result.name}: {phase_result.status}")

asyncio.run(main())
```

## 4. Add a Custom UICallback

Replace `NullCallback` with your own implementation to get real-time feedback:

```python
from collections.abc import Sequence
from orchcore.pipeline import Phase, PhaseResult, PipelineResult
from orchcore.stream import StreamEvent, StreamEventType, AgentResult
from orchcore.ui import UICallback

class SimpleUI:
    def on_pipeline_start(self, phases: Sequence[Phase]) -> None:
        print(f"Starting {len(phases)} phases")

    def on_phase_start(self, phase: Phase) -> None:
        print(f"\n--- Phase: {phase.name} ---")

    def on_agent_start(self, agent_name: str, phase: str) -> None:
        print(f"  Agent {agent_name} starting...")

    def on_agent_event(self, event: StreamEvent) -> None:
        if event.event_type == StreamEventType.TOOL_START:
            print(f"    Tool: {event.tool_name}")

    def on_agent_complete(self, agent_name: str, result: AgentResult) -> None:
        print(f"  Agent {agent_name} done (exit={result.exit_code})")

    def on_phase_end(self, phase: Phase, result: PhaseResult) -> None:
        print(f"  Phase {phase.name}: {result.status}")

    def on_pipeline_complete(self, result: PipelineResult) -> None:
        print(f"\nPipeline {'succeeded' if result.success else 'failed'}")

    # Remaining methods can be no-ops
    def on_phase_skip(self, phase: Phase, reason: str) -> None: pass
    def on_agent_error(self, agent_name: str, error: str) -> None: pass
    def on_stall_detected(self, agent_name: str, duration: float) -> None: pass
    def on_rate_limit(self, agent_name: str, message: str) -> None: pass
    def on_rate_limit_wait(self, agent_name: str, wait_seconds: float) -> None: pass
    def on_retry(self, agent_name: str, attempt: int, max_attempts: int) -> None: pass
    def on_git_recovery(self, action: str, detail: str) -> None: pass
    def on_shutdown(self, reason: str) -> None: pass
```

See the [Writing a UICallback](../guides/writing-a-uicallback.md) guide for a complete walkthrough.

## 5. Configure Settings

Create an `orchcore.toml` for project-level settings:

```toml
concurrency = 4
stall_timeout = 300
max_retries = 3
log_level = "info"

[profiles.fast]
max_retries = 1
stall_timeout = 60

[profiles.deep]
stall_timeout = 900
deep_tool_timeout = 1800
```

See the [Configuration Reference](../reference/configuration.md) for all settings.

## Next Steps

- [Configuration Reference](../reference/configuration.md) — all settings, profiles, and env vars
- [Stream Events Reference](../reference/stream-events.md) — understand the event model
- [Agent Registry guide](../guides/agent-registry.md) — advanced agent configuration
- [Recovery & Retry guide](../guides/recovery-and-retry.md) — rate limits, backoff, failure modes
- [Architecture Overview](../architecture/overview.md) — understand how modules fit together
