# Writing a UICallback

This guide walks you through implementing a custom `UICallback` for your consuming project. The UICallback protocol is how orchcore communicates pipeline progress to your application — you control what happens with each event.

## How It Works

orchcore's engine calls `UICallback` methods at key lifecycle points. Your implementation decides what to do: print to the terminal, update a TUI widget, log to a file, send a webhook, or nothing at all.

```
PipelineRunner ──▶ on_pipeline_start()
  PhaseRunner ──▶ on_phase_start()
    AgentRunner ──▶ on_agent_start()
                    on_agent_event()  ← called for every StreamEvent
                    on_agent_event()
                    ...
                    on_agent_complete() or on_agent_error()
                ──▶ on_phase_end()
              ──▶ on_pipeline_complete()
```

## Minimal Implementation

Start with a class that implements all 15 methods. Methods you don't need can be no-ops:

```python
from collections.abc import Sequence
from orchcore.pipeline import Phase, PhaseResult, PipelineResult
from orchcore.stream import StreamEvent, StreamEventType, AgentResult


class MinimalUI:
    """Bare-minimum UICallback — prints phase and agent lifecycle."""

    def on_pipeline_start(self, phases: Sequence[Phase]) -> None:
        print(f"Pipeline: {len(phases)} phases")

    def on_pipeline_complete(self, result: PipelineResult) -> None:
        status = "OK" if result.success else "FAILED"
        print(f"Pipeline {status} in {result.total_duration}")

    def on_phase_start(self, phase: Phase) -> None:
        mode = "parallel" if phase.parallel else "sequential"
        print(f"\n[{phase.name}] ({mode}, {len(phase.agents)} agents)")

    def on_phase_end(self, phase: Phase, result: PhaseResult) -> None:
        print(f"[{phase.name}] {result.status}")

    def on_phase_skip(self, phase: Phase, reason: str) -> None:
        print(f"[{phase.name}] SKIPPED: {reason}")

    def on_agent_start(self, agent_name: str, phase: str) -> None:
        print(f"  {agent_name} starting...")

    def on_agent_event(self, event: StreamEvent) -> None:
        pass  # High-frequency — keep this fast

    def on_agent_complete(self, agent_name: str, result: AgentResult) -> None:
        print(f"  {agent_name} done (exit={result.exit_code})")

    def on_agent_error(self, agent_name: str, error: str) -> None:
        print(f"  {agent_name} ERROR: {error}")

    def on_stall_detected(self, agent_name: str, duration: float) -> None:
        print(f"  {agent_name} STALL ({duration:.0f}s)")

    def on_rate_limit(self, agent_name: str, message: str) -> None:
        print(f"  {agent_name} RATE LIMITED: {message}")

    def on_rate_limit_wait(self, agent_name: str, wait_seconds: float) -> None:
        print(f"  {agent_name} waiting {wait_seconds:.0f}s...")

    def on_retry(self, agent_name: str, attempt: int, max_attempts: int) -> None:
        print(f"  {agent_name} retry {attempt}/{max_attempts}")

    def on_git_recovery(self, action: str, detail: str) -> None:
        print(f"  Git {action}: {detail}")

    def on_shutdown(self, reason: str) -> None:
        print(f"Shutdown: {reason}")
```

## Rich Terminal UI Example

A more sophisticated implementation using [Rich](https://rich.readthedocs.io/):

```python
from rich.console import Console
from rich.live import Live
from rich.table import Table
from collections.abc import Sequence
from orchcore.pipeline import Phase, PhaseResult, PipelineResult
from orchcore.stream import StreamEvent, StreamEventType, AgentResult

console = Console(stderr=True)


class RichUI:
    """Rich-powered terminal UI with colored output."""

    def on_pipeline_start(self, phases: Sequence[Phase]) -> None:
        console.rule(f"[bold]Pipeline — {len(phases)} phases[/bold]")

    def on_pipeline_complete(self, result: PipelineResult) -> None:
        style = "green" if result.success else "red"
        console.rule(f"[{style}]Pipeline {'succeeded' if result.success else 'failed'}[/{style}]")
        if result.total_cost_usd:
            console.print(f"  Cost: ${result.total_cost_usd:.4f}")
        console.print(f"  Duration: {result.total_duration}")

    def on_phase_start(self, phase: Phase) -> None:
        mode = "[cyan]parallel[/cyan]" if phase.parallel else "sequential"
        console.print(f"\n[bold]{phase.name}[/bold] ({mode})")

    def on_phase_end(self, phase: Phase, result: PhaseResult) -> None:
        style = "green" if result.status == "done" else "yellow"
        console.print(f"  [{style}]{phase.name}: {result.status}[/{style}]")

    def on_phase_skip(self, phase: Phase, reason: str) -> None:
        console.print(f"  [dim]{phase.name}: skipped — {reason}[/dim]")

    def on_agent_start(self, agent_name: str, phase: str) -> None:
        console.print(f"  [blue]{agent_name}[/blue] starting")

    def on_agent_event(self, event: StreamEvent) -> None:
        if event.event_type == StreamEventType.TOOL_START:
            detail = f" ({event.tool_detail})" if event.tool_detail else ""
            console.print(f"    [dim]{event.tool_name}{detail}[/dim]")

    def on_agent_complete(self, agent_name: str, result: AgentResult) -> None:
        cost = f" ${result.cost_usd:.4f}" if result.cost_usd else ""
        console.print(f"  [green]{agent_name} done[/green]{cost}")

    def on_agent_error(self, agent_name: str, error: str) -> None:
        console.print(f"  [red]{agent_name} error: {error}[/red]")

    def on_stall_detected(self, agent_name: str, duration: float) -> None:
        console.print(f"  [yellow]{agent_name} stalled ({duration:.0f}s)[/yellow]")

    def on_rate_limit(self, agent_name: str, message: str) -> None:
        console.print(f"  [yellow]{agent_name} rate limited[/yellow]")

    def on_rate_limit_wait(self, agent_name: str, wait_seconds: float) -> None:
        console.print(f"  [yellow]{agent_name} waiting {wait_seconds:.0f}s[/yellow]")

    def on_retry(self, agent_name: str, attempt: int, max_attempts: int) -> None:
        console.print(f"  [yellow]{agent_name} retry {attempt}/{max_attempts}[/yellow]")

    def on_git_recovery(self, action: str, detail: str) -> None:
        console.print(f"  [cyan]git {action}: {detail}[/cyan]")

    def on_shutdown(self, reason: str) -> None:
        console.print(f"[bold red]Shutdown: {reason}[/bold red]")
```

## Performance Considerations

`on_agent_event` is called for **every** stream event that passes the filter — potentially hundreds per second. Keep this method fast:

- Avoid I/O or network calls in `on_agent_event`
- Buffer updates and flush periodically (e.g., every 100ms)
- Use `event.event_type` to filter early — only process the events you care about

```python
def on_agent_event(self, event: StreamEvent) -> None:
    # Only react to tool lifecycle events
    if event.event_type not in (StreamEventType.TOOL_START, StreamEventType.TOOL_DONE):
        return
    # ... update display
```

## Testing Your UICallback

Use `isinstance` to verify your implementation satisfies the protocol:

```python
from orchcore.ui import UICallback

ui = RichUI()
assert isinstance(ui, UICallback)  # Runtime protocol check
```

For unit tests, use `NullCallback` as a baseline and test your implementation against known event sequences from the test fixtures.

## Related

- [UICallback Protocol Reference](../reference/ui-callback.md) — complete method signatures
- [Stream Events Reference](../reference/stream-events.md) — the events passed to `on_agent_event`
- [ADR-003: Protocol-based UI decoupling](../architecture/adrs/003-protocol-based-ui-decoupling.md)
