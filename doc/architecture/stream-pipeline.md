# Stream Pipeline

orchcore's stream processing pipeline normalizes output from 5 different agent CLI JSONL formats into a unified `StreamEvent` model through four composable stages. Each stage is independently usable.

## Pipeline Overview

```
Raw JSONL ──▶ StreamFilter ──▶ StreamParser ──▶ AgentMonitor ──▶ StallDetector ──▶ StreamEvent
               (drop ~95%       (format →        (9-state         (timeout +
                noise lines)     unified event)   state machine)   deep-tool aware)
```

## Stage 1: StreamFilter

**Purpose:** Fast-path pre-parse noise reduction.

The filter operates on raw strings *before* `json.loads()` is called, using simple substring matching to drop high-volume, low-value events. This avoids the cost of parsing JSON for lines that will be discarded anyway.

**Performance:** Drops approximately 95% of raw JSONL lines for typical Claude output.

**Format-specific skip patterns:**

| Format | Skipped Event Types |
|--------|-------------------|
| Claude | `message_start`, `message_stop`, `content_block_stop`, `content_block_start` (non-tool), `ping` |
| Codex | Heartbeat-only messages, empty delta events |
| Gemini | Connection metadata, keep-alive pings |
| Copilot | Status polling responses, empty chunks |
| OpenCode | Frame events without content |

```python
from orchcore.stream import StreamFilter, StreamFormat

filter = StreamFilter(format=StreamFormat.CLAUDE)
for line in raw_jsonl_lines:
    if filter.should_keep(line):
        # Worth parsing
        ...
```

## Stage 2: StreamParser

**Purpose:** Format-specific JSONL parsing into normalized `StreamEvent` instances.

Each agent CLI emits JSONL in a different schema. The parser contains format-specific logic for all 5 supported formats, producing a unified `StreamEvent` regardless of source.

**Supported formats:** Claude, Codex, OpenCode, Gemini, Copilot

**Key behaviors:**

- Handles malformed JSON gracefully with bounded warning logs
- Extracts tool invocations, text content, cost, tokens, and exit codes
- Supports native text extraction (no `jq` binary required)
- Maps format-specific event types to the unified `StreamEventType` enum

```python
from orchcore.stream import StreamParser, StreamFormat

parser = StreamParser(format=StreamFormat.CLAUDE)
event = parser.parse(json_line)  # Returns StreamEvent | None
```

## Stage 3: AgentMonitor

**Purpose:** Real-time state tracking via a 9-state machine.

The monitor consumes `StreamEvent` instances and maintains a running model of the agent's lifecycle:

```
STARTING → THINKING → WRITING
                ↓         ↓
          TOOL_RUNNING ───┘
                ↓
           STALLED / RATE_LIMITED
                ↓
     COMPLETED / FAILED / CANCELLED
```

**What it tracks:**

- Current agent state
- Active and recently completed tools (deque, max 20)
- Tool counters (total, succeeded, failed, running)
- Cumulative cost and token usage
- Friendly tool names (e.g., `"Read"` → `"Read file"`, `"Bash"` → `"Shell command"`)

**Snapshots** — call `snapshot()` to get an `AgentMonitorSnapshot` capturing the current state. Useful for building dashboards and status displays.

```python
from orchcore.stream import AgentMonitor

monitor = AgentMonitor(agent_name="claude")
for event in events:
    monitor.update(event)
    snap = monitor.snapshot()
    print(f"{snap.state} | Tools: {snap.counters.total} | Cost: ${snap.cost_usd}")
```

## Stage 4: StallDetector

**Purpose:** Timeout detection with deep-tool awareness.

The stall detector wraps the event stream and injects synthetic `STALL` events when an agent has been idle beyond a configurable timeout.

**Two timeout tiers:**

| Tier | Default | When Used |
|------|---------|-----------|
| Normal | 300 seconds | Standard tool executions |
| Deep tool | 600 seconds | Long-running tools (Exa, Tavily, web search, crawling) |

**Deep-tool recognition:** Case-insensitive substring matching against tool names. If the active tool matches a known deep-tool pattern (e.g., `exa`, `tavily`, `web_search`, `crawl`), the extended timeout applies.

**Heartbeat re-arming:** Any event from the agent resets the stall timer. The detector only fires after continuous silence.

```python
from orchcore.stream import StallDetector

detector = StallDetector(
    stall_timeout=300.0,
    deep_tool_timeout=600.0,
)
# Wraps an async event stream, injecting STALL events on timeout
```

## Full Pipeline Integration

In production, the four stages are composed by `AgentRunner`, which wires them together for each subprocess:

```python
# Simplified internal flow in AgentRunner
filter = StreamFilter(format=agent.stream_format)
parser = StreamParser(format=agent.stream_format)
monitor = AgentMonitor(agent_name=agent.name)
detector = StallDetector(
    stall_timeout=agent.stall_timeout,
    deep_tool_timeout=agent.deep_tool_timeout,
)

async for line in subprocess.stdout:
    if not filter.should_keep(line):
        continue
    event = parser.parse(line)
    if event is None:
        continue
    monitor.update(event)
    detector.heartbeat()
    callback.on_agent_event(event)
```

## Using Stages Independently

Each stage is a standalone component. You can use any subset:

- **Just the parser** — normalize JSONL from a log file without real-time monitoring
- **Filter + Parser** — efficient batch processing of stored streams
- **Monitor only** — feed pre-parsed events to track state
- **Full pipeline** — real-time subprocess monitoring with stall detection

## Related

- [Stream Events Reference](../reference/stream-events.md) — complete field-level documentation
- [Architecture Overview](overview.md) — how the stream pipeline fits into the broader system
- [ADR-004: Composable stream processing pipeline](adrs/004-composable-stream-processing-pipeline.md)
