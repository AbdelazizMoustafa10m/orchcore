# Stream Pipeline

The stream pipeline turns the JSONL output of five agent CLI formats into `StreamEvent` objects in four stages. Each stage can also be used on its own (see [Using Stages Independently](#using-stages-independently)).

## Pipeline Overview

```
Raw JSONL ──▶ StreamFilter ──▶ StreamParser ──▶ AgentMonitor ──▶ StallDetector ──▶ StreamEvent
               (drop ~95%       (format →        (9-state         (timeout +
                noise lines)     unified event)   state machine)   deep-tool aware)
```

## Stage 1: StreamFilter

`StreamFilter` drops high-volume, low-value lines before they are parsed. It works on the raw string with substring matching, before `json.loads()` is called, so discarded lines are never parsed. For typical Claude output, it is estimated to drop about 95% of lines.

A line is dropped if it is empty or whitespace-only, or if it matches one of the format's skip patterns (with or without a space after the colon). Every other line is kept, including malformed ones, so the parser can count them.

| Format | Skipped Lines |
|--------|---------------|
| Claude | `content_block_stop`, `message_start`, and `message_stop` events |
| Codex | `response.output_text.delta`, `response.reasoning_summary.delta`, and `turn.started` events |
| OpenCode | `text` events with an empty `text` field |
| Antigravity | `user_input` step echoes |
| Copilot | Lines with an empty `text`, `message`, or `content` field |

```python
from orchcore.stream import StreamFilter, StreamFormat

filter = StreamFilter(stream_format=StreamFormat.CLAUDE)
for line in raw_jsonl_lines:
    if filter.should_keep(line):
        # Pass the line to the parser
        ...
```

## Stage 2: StreamParser

`StreamParser` turns each JSONL line into normalized `StreamEvent` instances. Each agent CLI emits JSONL in a different schema. Format knowledge lives in one module per
format under `orchcore/stream/formats/` (`claude.py`, `codex.py`, `copilot.py`, `opencode.py`,
`antigravity.py`), each validating lines into private Pydantic envelope models (`extra="allow"` for
forward compatibility) and producing zero or more normalized `StreamEvent` objects per line.
`StreamParser` itself is a thin dispatcher that owns the shared infrastructure: line splitting
and the two diagnostic counters.

The parser behaves as follows:

- Malformed JSON lines are skipped and counted in `json_parse_error_count`, with a limited
  number of warnings logged.
- Well-formed lines that fail the format's wire schema are counted in
  `wire_validation_error_count`, with the same limit on warnings. Both counters are reported
  on `AgentResult`, so schema failures are never silent.
- Tool invocations, text content, cost, tokens, and exit codes are extracted into event fields.
- Text is extracted natively, without a `jq` binary.
- Format-specific event types are mapped to the `StreamEventType` enum.
- Antigravity tool steps get `<conversation_id>:<step_index>` IDs, so the start and end of
  overlapping tools and subagent steps are matched exactly. A failed step finishes with
  `tool_status="error"`.

```python
from orchcore.stream import StreamParser, StreamFormat

parser = StreamParser(stream_format=StreamFormat.CLAUDE)
events = parser.parse_line(json_line)  # Returns list[StreamEvent]
```

## Stage 3: AgentMonitor

`AgentMonitor` consumes `StreamEvent` instances and tracks the agent's lifecycle in a nine-state machine:

```
STARTING → THINKING → WRITING
                ↓         ↓
          TOOL_RUNNING ───┘
                ↓
           STALLED / RATE_LIMITED
                ↓
     COMPLETED / FAILED / CANCELLED
```

For each agent, it tracks

- the current state,
- the active tools and the 20 most recently completed ones,
- tool counters (total, succeeded, failed, running),
- cumulative cost and token usage, and
- display names for tools (e.g., `"Read"` → `"Read file"`, `"Bash"` → `"Shell command"`).

`snapshot()` returns an `AgentMonitorSnapshot` of the current state for dashboards and status displays.

```python
from orchcore.stream import AgentMonitor

monitor = AgentMonitor(agent_name="claude")
for event in events:
    monitor.update(event)
    snap = monitor.snapshot()
    print(f"{snap.state} | Tools: {snap.counters.total} | Cost: ${snap.cost_usd}")
```

## Stage 4: StallDetector

`StallDetector` wraps the event stream and injects a synthetic `STALL` event when the agent has been silent for longer than the timeout. It uses one of two timeouts:

| Timeout | Default | When Used |
|---------|---------|-----------|
| Normal | 300 seconds | No deep tool is active |
| Deep tool | 600 seconds | A long-running deep tool (e.g., Exa or Tavily search) is active |

A tool counts as a deep tool if its name contains one of the patterns in `StallDetector.DEEP_TOOL_PATTERNS`, compared case-insensitively. The patterns include `deep_search`, `deep_research`, `web_search_exa`, `tavily_search`, and `tavily_crawl`. For example, `mcp__exa__deep_search_exa` matches `deep_search`. The deep timeout applies while any active tool matches.

Every event from the agent resets the stall timer, so a `STALL` event always means continuous silence. The detector checks the timer every `check_interval` seconds (default 5) and emits at most one `STALL` event per silent period.

```python
from orchcore.stream import StallDetector

detector = StallDetector(
    normal_timeout=300.0,
    deep_timeout=600.0,
)
# Wraps an async event stream, injecting STALL events on timeout
```

## Full Pipeline Integration

`AgentRunner` composes the four stages for each subprocess:

```python
# Simplified internal flow in AgentRunner
filter = StreamFilter(stream_format=agent.stream_format)
parser = StreamParser(stream_format=agent.stream_format)
monitor = AgentMonitor(agent_name=agent.name)
detector = StallDetector(
    normal_timeout=agent.stall_timeout,
    deep_timeout=agent.deep_tool_timeout,
)

# Stages compose as async iterators — each wraps the previous
raw_lines = read_lines(proc.stdout)
filtered = filter.filter_stream(raw_lines)
parsed = parser.parse_stream(filtered)
watched = detector.watch(parsed)          # injects STALL events on silence

await monitor.consume(watched, on_event=callback.on_agent_event)
```

## Using Stages Independently

Each stage is a standalone component, and any subset can be used:

- the parser alone, to normalize JSONL from a log file,
- the filter and the parser, to process stored streams in batch,
- the monitor alone, to track state from events parsed elsewhere, or
- all four stages, to monitor a live subprocess with stall detection.

## Related

- [Stream Events Reference](../reference/stream-events.md): every event type and field
- [Architecture Overview](overview.md): how the stream pipeline fits with the other modules
- [ADR-004: Composable stream processing pipeline](adrs/004-composable-stream-processing-pipeline.md)
