# Signal Handling

orchcore's `SignalManager` traps SIGINT/SIGTERM and exposes a cooperative shutdown flag. It does not cancel tasks or terminate subprocesses itself — consumers check the flag and act accordingly.

## Overview

`SignalManager` handles:

- **Shutdown flag** — first signal sets `shutdown_requested = True`; consumers poll it
- **Forced exit** — second SIGINT raises `KeyboardInterrupt`
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

## Relationship to PhaseRunner

`PhaseRunner` installs its own SIGINT/SIGTERM handlers via `loop.add_signal_handler()` to terminate active subprocesses on the first signal and force-kill on the second. It does **not** use `SignalManager` — the two are independent implementations.

`SignalManager` is designed for consuming projects that need signal handling outside the pipeline execution scope (e.g., wrapping the entire application lifecycle, coordinating shutdown across multiple subsystems, or running custom cleanup before the pipeline even starts):

```python
import asyncio
from orchcore.signals import SignalManager

async def main() -> None:
    async with SignalManager() as signals:
        # Custom pre-pipeline setup that also needs graceful shutdown
        await setup_resources()

        if not signals.shutdown_requested:
            await run_pipeline()

        # Custom post-pipeline cleanup
        await teardown_resources()

asyncio.run(main())
```

## Error Handling

If no event loop is running when `SignalManager.__aenter__` is called (e.g., during testing), signal handlers are not installed and the context manager becomes a no-op. This avoids `RuntimeError` in non-async contexts.

## Related

- [Architecture Overview](../architecture/overview.md) — how signal handling fits into the broader system
- [Recovery & Retry](recovery-and-retry.md) — how recovery interacts with graceful shutdown
