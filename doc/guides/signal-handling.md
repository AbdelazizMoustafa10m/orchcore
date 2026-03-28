# Signal Handling

orchcore's `SignalManager` provides graceful SIGINT/SIGTERM shutdown with async task cancellation and subprocess cleanup.

## Overview

When orchestrating long-running agent subprocesses, clean shutdown matters — you don't want orphaned processes, corrupted output files, or lost progress. `SignalManager` handles:

- **Graceful shutdown** — first signal requests cancellation, allowing cleanup
- **Forced exit** — second SIGINT forces immediate exit via `KeyboardInterrupt`
- **SIGTERM support** — participates in graceful shutdown but never forces exit
- **Signal restoration** — original handlers are restored on context exit

## Basic Usage

`SignalManager` is an async context manager that installs signal handlers on entry and restores them on exit:

```python
import asyncio
from orchcore.signals import SignalManager

async def main() -> None:
    async with SignalManager() as signals:
        # Run your pipeline here
        # First Ctrl+C triggers graceful shutdown
        # Second Ctrl+C forces exit
        await run_pipeline(signals)

asyncio.run(main())
```

## Checking for Shutdown

Use `shutdown_requested` or `check_shutdown()` to cooperatively respond to signals:

```python
async def run_pipeline(signals: SignalManager) -> None:
    for phase in phases:
        # Poll before starting each phase
        signals.check_shutdown()  # Raises CancelledError if signal received
        await run_phase(phase)
```

```python
async def run_pipeline(signals: SignalManager) -> None:
    for phase in phases:
        if signals.shutdown_requested:
            print("Shutdown requested, stopping after current phase")
            break
        await run_phase(phase)
```

## Signal Behavior

| Signal | First | Second |
|--------|-------|--------|
| SIGINT (Ctrl+C) | Sets `shutdown_requested = True` | Raises `KeyboardInterrupt` |
| SIGTERM | Sets `shutdown_requested = True` | Sets `shutdown_requested = True` (no forced exit) |

This two-tier design lets consumers decide how to handle graceful shutdown (save state, archive partial results, terminate subprocesses) before a forced exit occurs.

## Integration with Pipeline Execution

In a typical orchcore pipeline, `SignalManager` is used at the top level. `PipelineRunner` and `PhaseRunner` check for shutdown between phases and before launching new agents:

```python
from orchcore.signals import SignalManager
from orchcore.pipeline import PipelineRunner, PhaseRunner
from orchcore.runner import AgentRunner
from orchcore.registry import AgentRegistry

async def main() -> None:
    async with SignalManager() as signals:
        registry = AgentRegistry()
        runner = AgentRunner()
        phase_runner = PhaseRunner(runner, registry)
        pipeline = PipelineRunner(phase_runner)

        result = await pipeline.run_pipeline(
            phases=phases,
            prompts=prompts,
            ui_callback=callback,
        )
```

## Error Handling

If no event loop is running when `SignalManager.__aenter__` is called (e.g., during testing), signal handlers are not installed and the context manager becomes a no-op. This avoids `RuntimeError` in non-async contexts.

## Related

- [Architecture Overview](../architecture/overview.md) — how signal handling fits into the broader system
- [Recovery & Retry](recovery-and-retry.md) — how recovery interacts with graceful shutdown
