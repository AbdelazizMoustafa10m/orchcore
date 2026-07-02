"""Minimal orchcore pipeline setup.

Requires a matching ``agents.toml`` file and the configured agent CLI on
``PATH``. Use ``dry_run=True`` at the AgentRunner layer for artifact-level
smoke tests; a real pipeline run launches the configured CLI.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from orchcore.pipeline import Phase, PhaseRunner, PipelineRunner
from orchcore.registry import AgentRegistry, ToolSet
from orchcore.runner import AgentRunner
from orchcore.ui import NullCallback


async def main() -> None:
    registry = AgentRegistry()
    registry.load_from_toml(Path("agents.toml"))

    phase = Phase(
        name="planning",
        agents=("claude",),
        # Selects [agents.<name>.flags].plan — a name YOUR project defines.
        flag_profile="plan",
        tools=ToolSet(internal=("Read", "Glob", "Grep"), permission="read-only"),
    )

    runner = AgentRunner()
    phase_runner = PhaseRunner(runner, registry, max_concurrency=4)
    pipeline = PipelineRunner(phase_runner)

    result = await pipeline.run_pipeline(
        phases=[phase],
        prompts={"planning": "Analyze the codebase and create a plan."},
        ui_callback=NullCallback(),
    )

    print(f"Success: {result.success} | Cost: ${result.total_cost_usd}")


if __name__ == "__main__":
    asyncio.run(main())
