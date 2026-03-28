# Stream Events Reference

orchcore normalizes output from 5 different agent CLI formats into a unified event model. This page documents all event types, fields, and related models.

## StreamFormat

Identifies the JSONL output format of an agent CLI.

| Value | Agent CLI |
|-------|-----------|
| `claude` | Claude Code |
| `codex` | Codex |
| `opencode` | OpenCode |
| `gemini` | Gemini CLI |
| `copilot` | Copilot CLI |

```python
from orchcore.stream import StreamFormat

format = StreamFormat.CLAUDE  # "claude"
```

## StreamEventType

Rich event taxonomy covering the full agent lifecycle.

| Value | Description |
|-------|-------------|
| `init` | Agent process started |
| `state` | Agent state changed (see `AgentState`) |
| `heartbeat` | Keep-alive signal from the agent |
| `tool_start` | Agent began executing a tool |
| `tool_exec` | Tool execution in progress (intermediate update) |
| `tool_done` | Tool execution completed |
| `text` | Agent emitted text output |
| `subagent` | Agent spawned a sub-agent |
| `result` | Agent completed with final result |
| `error` | Agent encountered an error |
| `cancelled` | Agent was cancelled (e.g., via SIGINT) |
| `retry` | Agent is being retried after failure |
| `rate_limit` | Agent hit a rate limit |
| `stall` | Agent stalled (no activity for timeout period) |

## StreamEvent

The core normalized event model. Every JSONL line from any agent format is parsed into a `StreamEvent`.

```python
from orchcore.stream import StreamEvent, StreamEventType
```

### Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `event_type` | `StreamEventType` | *(required)* | Event category |
| `timestamp` | `datetime` | `now(UTC)` | When the event occurred (timezone-aware) |
| `tool_id` | `str \| None` | `None` | Unique identifier for a tool invocation |
| `tool_name` | `str \| None` | `None` | Tool name (e.g., `"Read"`, `"Bash"`, `"Write"`) |
| `tool_detail` | `str \| None` | `None` | Tool-specific detail (file path, command, etc.) |
| `tool_status` | `"running" \| "done" \| "error" \| None` | `None` | Current tool execution status |
| `tool_duration_ms` | `int \| None` | `None` | Tool execution duration in milliseconds |
| `text_preview` | `str \| None` | `None` | Truncated text content for display |
| `text_full` | `str \| None` | `None` | Complete text content |
| `cost_usd` | `Decimal \| None` | `None` | Cumulative cost in USD |
| `duration_ms` | `int \| None` | `None` | Total agent execution duration |
| `exit_code` | `int \| None` | `None` | Process exit code (on `result` events) |
| `num_turns` | `int \| None` | `None` | Number of conversation turns |
| `session_id` | `str \| None` | `None` | Agent session identifier |
| `token_usage` | `dict[str, int] \| None` | `None` | Token counts (e.g., `{"input": 1000, "output": 500}`) |
| `error` | `str \| None` | `None` | Error message (on `error` events) |
| `retry_attempt` | `int \| None` | `None` | Current retry attempt number |
| `retry_max` | `int \| None` | `None` | Maximum retry attempts |
| `retry_delay_ms` | `int \| None` | `None` | Delay before next retry |
| `error_category` | `str \| None` | `None` | Error classification |
| `idle_seconds` | `float \| None` | `None` | Seconds since last activity (on `stall` events) |
| `raw` | `dict \| None` | `None` | Original parsed JSON for debugging |

`StreamEvent` is a frozen Pydantic model — instances are immutable after creation.

## AgentState

9-state machine tracking an agent's lifecycle.

```
STARTING → THINKING → WRITING
                ↓         ↓
          TOOL_RUNNING ───┘
                ↓
           STALLED / RATE_LIMITED
                ↓
     COMPLETED / FAILED / CANCELLED
```

| State | Description |
|-------|-------------|
| `starting` | Agent subprocess has been launched |
| `thinking` | Agent is processing (no tool active) |
| `writing` | Agent is emitting text output |
| `tool_running` | Agent is executing a tool |
| `stalled` | No activity detected for timeout period |
| `rate_limited` | Agent hit a rate limit |
| `completed` | Agent finished successfully |
| `failed` | Agent exited with an error |
| `cancelled` | Agent was terminated by signal |

## ToolExecution

Tracks a single tool invocation through its lifecycle.

| Field | Type | Description |
|-------|------|-------------|
| `tool_id` | `str` | Unique invocation identifier |
| `name` | `str` | Raw tool name |
| `friendly_name` | `str` | Human-readable name (e.g., `"Read"` → `"Read file"`) |
| `detail` | `str \| None` | Tool-specific detail (file path, command) |
| `started_at` | `datetime` | When the tool started |
| `completed_at` | `datetime \| None` | When the tool finished |
| `status` | `"running" \| "done" \| "error"` | Current status |
| `duration` | `timedelta \| None` | Execution time |

## ToolCounters

Aggregated tool execution statistics.

| Field | Type | Description |
|-------|------|-------------|
| `total` | `int` | Total tool invocations |
| `succeeded` | `int` | Successfully completed |
| `failed` | `int` | Failed with error |
| `running` | `int` | Currently in progress |

## AgentMonitorSnapshot

Point-in-time state of a monitored agent. Useful for building dashboards and status displays.

| Field | Type | Description |
|-------|------|-------------|
| `agent_name` | `str` | Agent identifier |
| `state` | `AgentState` | Current state |
| `elapsed` | `timedelta` | Time since agent started |
| `counters` | `ToolCounters` | Aggregated tool stats |
| `active_tools` | `list[ToolExecution]` | Currently running tools |
| `recent_tools` | `list[ToolExecution]` | Recently completed tools (max 20) |
| `last_tool` | `str \| None` | Name of most recent tool |
| `last_tool_detail` | `str \| None` | Detail of most recent tool |
| `cost_usd` | `Decimal \| None` | Cumulative cost |
| `token_usage` | `dict[str, int] \| None` | Token consumption |
| `text_count` | `int` | Number of text events |
| `subagent_count` | `int` | Number of sub-agent spawns |
| `idle_seconds` | `float` | Seconds since last activity |

## AgentResult

Return type of `AgentRunner.run()`. Captures all outputs from a single agent execution.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `agent_name` | `str` | `""` | Agent identifier |
| `output_path` | `Path \| None` | `None` | Path to extracted output file |
| `stream_path` | `Path \| None` | `None` | Path to raw JSONL stream |
| `log_path` | `Path \| None` | `None` | Path to log file |
| `exit_code` | `int` | `0` | Process exit code |
| `duration` | `timedelta \| None` | `None` | Execution duration |
| `cost_usd` | `Decimal \| None` | `None` | Total cost |
| `token_usage` | `dict[str, int] \| None` | `None` | Token counts |
| `num_turns` | `int \| None` | `None` | Conversation turns |
| `session_id` | `str \| None` | `None` | Session identifier |
| `output_empty` | `bool` | `False` | Whether output file was empty |
| `error` | `str \| None` | `None` | Error message if failed |

## Related

- [Stream Pipeline architecture](../architecture/stream-pipeline.md) — how the 4-stage pipeline works
- [ADR-004: Composable stream processing pipeline](../architecture/adrs/004-composable-stream-processing-pipeline.md)
- [ADR-006: Pydantic for all data models](../architecture/adrs/006-pydantic-for-all-data-models.md)
