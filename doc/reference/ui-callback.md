# UICallback Protocol Reference

`UICallback` is the protocol through which the orchestration engine reports progress to the presentation layer. It is a `runtime_checkable` Python `Protocol`: consuming projects implement it for their own UI (e.g., a Rich CLI, a Textual TUI, or headless JSONL output), and orchcore never imports a display framework.

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
| `on_pipeline_start` | Pipeline execution begins | `phases`: the ordered `Phase` definitions |
| `on_pipeline_complete` | Pipeline execution ends (success or failure) | `result`: `PipelineResult` with per-phase results |

### Phase Lifecycle

| Method | When Called | Parameters |
|--------|------------|------------|
| `on_phase_start` | A phase begins execution | `phase`: the `Phase` definition |
| `on_phase_end` | A phase completes | `phase`: the definition; `result`: `PhaseResult` |
| `on_phase_skip` | A phase is skipped (unmet dependency, `skip_phases`, resume, or FlowControl); phases excluded by `only_phase` produce no callback | `phase`: the definition; `reason`: human-readable explanation |

### Agent Lifecycle

| Method | When Called | Parameters |
|--------|------------|------------|
| `on_agent_start` | An agent subprocess is about to launch | `agent_name`; `phase`: name of the containing phase |
| `on_agent_event` | An agent emits a stream event | `event`: normalized `StreamEvent` |
| `on_agent_complete` | An agent run finishes, whether it succeeded or failed | `agent_name`; `result`: `AgentResult` |
| `on_agent_error` | An agent run fails; called before `on_agent_complete` | `agent_name`; `error`: error message |

### Recovery & Error Events

| Method | When Called | Parameters |
|--------|------------|------------|
| `on_stall_detected` | Agent has been idle beyond its timeout | `agent_name`; `duration`: idle time in seconds |
| `on_rate_limit` | Agent hit a rate limit and will be retried | `agent_name`; `message`: the rate-limit message |
| `on_rate_limit_wait` | Waiting for the rate limit to reset | `agent_name`; `wait_seconds`: time to wait |
| `on_retry` | Agent is about to be retried | `agent_name`; `attempt`: current attempt; `max_attempts` |
| `on_git_recovery` | Git recovery action taken or skipped | `action`: `"auto_commit"`, `"stash"`, `"stash_restore"`, `"stash_restore_failed"`, or `"skipped_no_cwd"`; `detail`: description |

### Shutdown

| Method | When Called | Parameters |
|--------|------------|------------|
| `on_shutdown` | Graceful shutdown initiated | `reason`: description; `PhaseRunner` passes `"Signal received"` on SIGINT or SIGTERM |

## Built-in Implementations

### NullCallback

Every method does nothing. Use it when you do not need UI feedback, e.g., in tests.

```python
from orchcore.ui import NullCallback

result = await pipeline.run_pipeline(
    phases=phases,
    prompts=prompts,
    ui_callback=NullCallback(),
)
```

### LoggingCallback

Logs every callback through Python's `logging` module: `INFO` for lifecycle events, `DEBUG` for stream events, `WARNING` for stalls, rate limits, and retries, and `ERROR` for agent errors.

```python
from orchcore.ui import LoggingCallback

result = await pipeline.run_pipeline(
    phases=phases,
    prompts=prompts,
    ui_callback=LoggingCallback(),
)
```

## Type Checking

Because `UICallback` is `runtime_checkable`, `isinstance` verifies an implementation at runtime. The check confirms that every method exists, not that the signatures match:

```python
from orchcore.ui import UICallback

class MyUI:
    # ... implement all methods ...
    pass

assert isinstance(MyUI(), UICallback)
```

For static type checking with mypy, annotate parameters with `UICallback`:

```python
async def run(callback: UICallback) -> None:
    callback.on_pipeline_start(phases)
```

## Related

- [Writing a UICallback guide](../guides/writing-a-uicallback.md): a minimal and a Rich implementation
- [ADR-003: Protocol-based UI decoupling](../architecture/adrs/003-protocol-based-ui-decoupling.md)
- [Stream Events Reference](stream-events.md): the `StreamEvent` model passed to `on_agent_event`
