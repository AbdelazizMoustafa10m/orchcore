# Observability

orchcore provides optional OpenTelemetry integration via `OrchcoreTelemetry`. When enabled, it emits traces for pipelines, phases, agents, and tool invocations. When OpenTelemetry packages are not installed, all methods silently become no-ops.

## Overview

`OrchcoreTelemetry` creates hierarchical spans that map to orchcore's execution model:

```
pipeline span
├── phase span (planning)
│   └── agent span (claude)
│       ├── tool span (Read)
│       └── tool span (Grep)
└── phase span (execution)
    ├── agent span (claude)
    └── agent span (codex)
```

## Setup

### Install OpenTelemetry Dependencies

OpenTelemetry is not a core dependency. The recommended installation path is the bundled telemetry extra:

```bash
uv pip install orchcore[telemetry]
```

This installs the tested OpenTelemetry API, SDK, and OTLP exporters for both gRPC and HTTP.

If you need a narrower dependency set, manual installation still works:

```bash
uv pip install opentelemetry-api opentelemetry-sdk
uv pip install opentelemetry-exporter-otlp-proto-grpc
# or
uv pip install opentelemetry-exporter-otlp-proto-http
```

### Initialize Telemetry

```python
from orchcore.observability import OrchcoreTelemetry

telemetry = OrchcoreTelemetry(
    enabled=True,
    service_name="my-orchestrator",       # Default: "orchcore"
    otlp_endpoint="http://localhost:4317", # Optional OTLP collector
    otlp_protocol="grpc",                 # "grpc" (default) or "http"
)
```

If `enabled=False` (default) or if OpenTelemetry packages are not installed, the object is safe to use — all methods become no-ops.

## Constructor Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `enabled` | `bool` | `False` | Enable telemetry. When `False`, all methods are no-ops |
| `service_name` | `str` | `"orchcore"` | OpenTelemetry service name attribute |
| `otlp_endpoint` | `str \| None` | `None` | OTLP collector endpoint. When `None`, no exporter is configured |
| `otlp_protocol` | `str` | `"grpc"` | Exporter protocol: `"grpc"` or `"http"` |

## Creating Spans

All span methods are context managers that yield the span object (or `None` when disabled):

```python
# Pipeline-level span
with telemetry.pipeline_span("my-pipeline", task_slug="fix-auth-bug") as span:

    # Phase-level span
    with telemetry.phase_span("planning", agent="claude") as span:

        # Agent-level span
        with telemetry.agent_span("planning", "claude") as span:

            # Tool-level span (requires a ToolExecution object)
            with telemetry.tool_span("claude", tool_execution) as span:
                ...
```

## Recording Cost

Record cost information on the current active span:

```python
from decimal import Decimal

telemetry.record_cost("claude", Decimal("0.42"))
```

This sets `orchcore.cost.claude` and `orchcore.cost.total` attributes on the current span.

## Graceful Degradation

`OrchcoreTelemetry` is designed to never break your pipeline:

- If OpenTelemetry packages are not installed → constructor logs a debug message, all methods become no-ops
- If OTLP exporter packages are missing → tracing works locally but spans are not exported
- If `record_cost` fails (span ended, invalid type) → a debug message is logged, no exception raised

## Integration Example

```python
from orchcore.observability import OrchcoreTelemetry

telemetry = OrchcoreTelemetry(
    enabled=True,
    service_name="planora",
    otlp_endpoint="http://jaeger:4317",
)

with telemetry.pipeline_span("planora-run", task_slug="implement-feature"):
    for phase in phases:
        with telemetry.phase_span(phase.name):
            for agent_name in phase.agents:
                with telemetry.agent_span(phase.name, agent_name):
                    result = await runner.run(agent, prompt, output_path)
                    telemetry.record_cost(agent_name, result.cost_usd)
```

## Related

- [Architecture Overview](../architecture/overview.md) — how observability fits into the broader system
