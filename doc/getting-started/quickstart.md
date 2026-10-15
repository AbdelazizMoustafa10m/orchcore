# Quick Start

This guide defines two agents in TOML, builds a two-phase pipeline, and runs it. It assumes orchcore and at least one agent CLI are installed (see [Installation](installation.md)).

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

# Optional: env_vars values are literal TOML strings. orchcore does not
# expand ${VAR}; load secrets in your own config layer before passing them.

[agents.claude.output_extraction]
strategy = "jq_filter"
jq_expression = ".content[0].text"

[agents.codex]
binary = "codex"
model = "o3"
subcommand = "exec"
stream_format = "codex"

# Keep only behavioral flags in profiles; tool access, permissions, and
# approval modes belong in each phase's ToolSet (they are dropped from
# profiles, with a warning, whenever a ToolSet is in effect).
[agents.codex.flags]
plan = ["-c", "model_reasoning_effort=high"]
fix = []

[agents.codex.output_extraction]
strategy = "stdout_capture"
```

Each `[agents.<name>]` section defines the agent's binary, model, the subcommand that precedes the prompt, named flag profiles, the stream format used to parse its output, and how to extract the final output. The profile names under `[agents.<name>.flags]` (`plan` and `fix` here) belong to your project's workflow vocabulary; orchcore attaches no meaning to them (see [Flag Profiles](../guides/agent-registry.md#flag-profiles)).

The [Agent Registry guide](../guides/agent-registry.md) lists all configuration options.

## 2. Define Execution Phases

A pipeline is a list of phases. Each phase specifies which agents run, whether they run sequentially or in parallel, and which tools they can use.

```python
from orchcore.pipeline import Phase
from orchcore.registry import ToolSet

planning = Phase(
    name="planning",
    agents=["claude"],
    parallel=False,
    flag_profile="plan",  # selects [agents.<name>.flags].plan for each agent
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
    flag_profile="fix",
    tools=ToolSet(
        internal=["Read", "Write", "Edit", "Bash"],
        mcp=[],
        permission="workspace-write",
        max_turns=25,
    ),
)
```

## 3. Run the Pipeline

Load the registry, build the runner stack, and run the pipeline:

```python
import asyncio
from pathlib import Path
from orchcore.pipeline import PipelineRunner, PhaseRunner
from orchcore.registry import AgentRegistry
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
    )

    print(f"Success: {result.success}")
    print(f"Duration: {result.total_duration}")
    print(f"Cost: ${result.total_cost_usd}")

    for phase_result in result.phases:
        print(f"  {phase_result.name}: {phase_result.status}")

asyncio.run(main())
```

Every phase that has agents needs an entry in `prompts`. For phases that intentionally run
without a prompt, pass `allow_empty_prompts=True` to `run_pipeline()`.

## 4. Add a Custom UICallback

To see progress while the pipeline runs, replace `NullCallback` with your own implementation. A `UICallback` implements all 15 methods, and the ones you do not need can be no-ops:

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

The [Writing a UICallback](../guides/writing-a-uicallback.md) guide covers a full implementation, including a Rich example.

## 5. Configure Settings

Project-level settings go in `orchcore.toml`:

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

orchcore's runners do not read these settings themselves. Load them with `load_settings_with_profile()` and pass the values to the components you construct, e.g., `PhaseRunner(runner, registry, max_concurrency=settings.concurrency)`. The [Configuration Reference](../reference/configuration.md) lists all settings and their sources.

## Next Steps

- [Configuration Reference](../reference/configuration.md): all settings, profiles, and environment variables.
- [Stream Events Reference](../reference/stream-events.md): the event model passed to `on_agent_event`.
- [Agent Registry guide](../guides/agent-registry.md): environment policy, version checks, prompt transport, and ToolSets.
- [Recovery & Retry guide](../guides/recovery-and-retry.md): rate limits, backoff, and failure modes.
- [Architecture Overview](../architecture/overview.md): how the modules fit together.
