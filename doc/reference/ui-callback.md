# UICallback Protocol Reference

The `UICallback` protocol is orchcore's central decoupling mechanism between the orchestration engine and the presentation layer. It is a `runtime_checkable` Python `Protocol` — consuming projects implement it for their specific UI (Rich CLI, Textual TUI, headless JSONL, etc.) without orchcore importing any display framework.

## Protocol Definition

```python
from typing import Protocol, runtime_checkable
from collections.abc import Sequence

@runtime_checkable
class UICallback(Protocol):
    def on_pipeline_start(self, phases: Sequence[Phase]) -> None: ...
    def on_pipeline_complete(self, result: PipelineResult) -> None: ...
    def on_phase_start(self, phase: Phase) -> None: ...
    def on_phase_end(self, phase: Phase, result: PhaseResult) -> None: ...
    def on_phase_skip(self, phase: Phase, reason: str) -> None: ...
    def on_agent_start(self, agent_name: str, phase: str) -> None: ...
    def on_agent_event(self, event: StreamEvent) -> None: ...
    def on_agent_complete(self, agent_name: str, result: AgentResult) -> None: ...
    def on_agent_error(self, agent_name: str, error: str) -> None: ...
    def on_stall_detected(self, agent_name: str, duration: float) -> None: ...
    def on_rate_limit(self, agent_name: str, message: str) -> None: ...
    def on_rate_limit_wait(self, agent_name: str, wait_seconds: float) -> None: ...
    def on_retry(self, agent_name: str, attempt: int, max_attempts: int) -> None: ...
    def on_git_recovery(self, action: str, detail: str) -> None: ...
    def on_shutdown(self, reason: str) -> None: ...
```

## Method Reference

### Pipeline Lifecycle

| Method | When Called | Parameters |
|--------|------------|------------|
| `on_pipeline_start` | Pipeline execution begins | `phases` — ordered list of `Phase` definitions |
| `on_pipeline_complete` | Pipeline execution ends (success or failure) | `result` — `PipelineResult` with per-phase results |

### Phase Lifecycle

| Method | When Called | Parameters |
|--------|------------|------------|
| `on_phase_start` | A phase begins execution | `phase` — the `Phase` definition |
| `on_phase_end` | A phase completes | `phase` — the definition; `result` — `PhaseResult` |
| `on_phase_skip` | A phase is skipped (dependency failed, filter applied) | `phase` — the definition; `reason` — human-readable explanation |

### Agent Lifecycle

| Method | When Called | Parameters |
|--------|------------|------------|
| `on_agent_start` | An agent subprocess is launched | `agent_name`; `phase` — name of the containing phase |
| `on_agent_event` | A stream event is emitted by an agent | `event` — normalized `StreamEvent` |
| `on_agent_complete` | An agent finishes successfully | `agent_name`; `result` — `AgentResult` |
| `on_agent_error` | An agent fails | `agent_name`; `error` — error message |

### Recovery & Error Events

| Method | When Called | Parameters |
|--------|------------|------------|
| `on_stall_detected` | Agent has been idle beyond timeout | `agent_name`; `duration` — idle time in seconds |
| `on_rate_limit` | Agent hit a rate limit | `agent_name`; `message` — the rate-limit message |
| `on_rate_limit_wait` | Waiting for rate-limit cooldown | `agent_name`; `wait_seconds` — time to wait |
| `on_retry` | Agent is being retried | `agent_name`; `attempt` — current attempt; `max_attempts` |
| `on_git_recovery` | Git recovery action taken | `action` — e.g., `"stash"`, `"commit"`; `detail` — description |

### Shutdown

| Method | When Called | Parameters |
|--------|------------|------------|
| `on_shutdown` | Graceful shutdown initiated | `reason` — e.g., `"SIGINT"`, `"SIGTERM"` |

## Built-in Implementations

### NullCallback

No-op implementation. All methods do nothing. Use when you don't need UI feedback (e.g., in tests).

```python
from orchcore.ui import NullCallback

result = await pipeline.run_pipeline(
    phases=phases,
    prompts=prompts,
    ui_callback=NullCallback(),
)
```

### LoggingCallback

Logs all events via Python's `logging` module at appropriate levels (`INFO` for lifecycle events, `DEBUG` for stream events, `WARNING` for rate limits and stalls, `ERROR` for agent errors).

```python
from orchcore.ui import LoggingCallback

result = await pipeline.run_pipeline(
    phases=phases,
    prompts=prompts,
    ui_callback=LoggingCallback(),
)
```

## Type Checking

`UICallback` is `runtime_checkable`, so you can verify implementations at runtime:

```python
from orchcore.ui import UICallback

class MyUI:
    # ... implement all methods ...
    pass

assert isinstance(MyUI(), UICallback)
```

For static type checking with mypy, simply use `UICallback` as a type annotation:

```python
async def run(callback: UICallback) -> None:
    callback.on_pipeline_start(phases)
```

## Related

- [Writing a UICallback guide](../guides/writing-a-uicallback.md) — step-by-step integration walkthrough
- [ADR-003: Protocol-based UI decoupling](../architecture/adrs/003-protocol-based-ui-decoupling.md)
- [Stream Events Reference](stream-events.md) — the `StreamEvent` model passed to `on_agent_event`
