# Signal Handling

`SignalManager` traps SIGINT and SIGTERM and exposes a cooperative shutdown flag. It does not cancel tasks or terminate subprocesses itself: your code checks the flag and decides what to stop. The first SIGINT or SIGTERM sets `shutdown_requested`, a second SIGINT raises `KeyboardInterrupt`, and SIGTERM never forces an exit (see [Signal Behavior](#signal-behavior)).

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

Check for a requested shutdown with `check_shutdown()`, which raises an exception, or by reading `shutdown_requested`:

```python
async def run_pipeline(signals: SignalManager) -> None:
    for phase in phases:
        # Poll before starting each phase
        signals.check_shutdown()  # Raises asyncio.CancelledError after a signal
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

Between the first signal and a forced exit, the application can save state, archive partial results, or terminate subprocesses.

## Relationship to PhaseRunner

`PhaseRunner` installs its own SIGINT and SIGTERM handlers with `loop.add_signal_handler()`. On the first signal it terminates the active agent subprocesses and calls `on_shutdown`. It force-kills any that are still running on the second signal or after a 30-second grace period. `PhaseRunner` does not use `SignalManager`; the two are independent.

Use `SignalManager` when your application needs a shutdown flag outside pipeline execution, e.g., to wrap the whole application lifecycle, to coordinate shutdown across several subsystems, or to run cleanup before the pipeline starts:

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

If no event loop is running when `SignalManager.__aenter__` is called (e.g., in some test setups), no handlers are installed and the context manager does nothing, instead of raising `RuntimeError`.

On Windows and other event loops that do not implement `loop.add_signal_handler()`,
`SignalManager` falls back to classic `signal.signal()` handlers and restores the
original handlers on exit. SIGINT works through this path; SIGTERM can be
registered but is not generally delivered by Windows process signaling.

## Related

- [Architecture Overview](../architecture/overview.md): how the signals module fits with the other modules
- [Recovery & Retry](recovery-and-retry.md): retries and git recovery, which stop when shutdown starts
