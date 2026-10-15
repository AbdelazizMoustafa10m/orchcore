# Observability

`OrchcoreTelemetry` emits OpenTelemetry traces for pipelines, phases, and agents. When it is disabled, or the OpenTelemetry packages are not installed, all of its methods do nothing.

## Span Hierarchy

The spans follow orchcore's execution model:

```
pipeline span
├── phase span (planning)
│   └── agent span (claude)
└── phase span (execution)
    ├── agent span (claude)
    └── agent span (codex)
```

## Setup

### Install OpenTelemetry Dependencies

OpenTelemetry is not a core dependency. The `telemetry` extra installs the OpenTelemetry API, the SDK, and the OTLP exporters for both gRPC and HTTP:

```bash
uv pip install "orchcore[telemetry]"
```

For a smaller dependency set, install the API, the SDK, and only the exporter you use:

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

With `enabled=False` (the default), or without the OpenTelemetry packages, the object can still be used and all of its methods do nothing.

When enabled, the constructor sets the process-wide OpenTelemetry tracer provider, so create one `OrchcoreTelemetry` per process.

Pass the object to `PhaseRunner(runner, registry, telemetry=telemetry)` to have the runner open the phase and agent spans itself. Wrap the `run_pipeline()` call in `pipeline_span()` to get the root span. `PhaseRunner` does not call `record_cost()`.

## Constructor Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `enabled` | `bool` | `False` | Enable telemetry. When `False`, all methods are no-ops |
| `service_name` | `str` | `"orchcore"` | OpenTelemetry service name attribute |
| `otlp_endpoint` | `str \| None` | `None` | OTLP collector endpoint. When `None`, no exporter is configured |
| `otlp_protocol` | `str` | `"grpc"` | Exporter protocol: `"grpc"` or `"http"` |

## Creating Spans

All span methods are context managers that yield the span object, or `None` when telemetry is disabled:

```python
# Pipeline-level span
with telemetry.pipeline_span("my-pipeline", task_slug="fix-auth-bug") as span:

    # Phase-level span
    with telemetry.phase_span("planning", agent="claude") as span:

        # Agent-level span
        with telemetry.agent_span("planning", "claude") as span:
            ...
```

## Recording Cost

`record_cost()` records an agent's cost on the current span. The cost must be a `Decimal`; check `AgentResult.cost_usd` for `None` before passing it:

```python
from decimal import Decimal

telemetry.record_cost("claude", Decimal("0.42"))
```

This sets `orchcore.cost.claude` on the current span. When called inside a
`pipeline_span`, it also sets `orchcore.cost.total` to the running pipeline total
at recording time.

## Graceful Degradation

`OrchcoreTelemetry` handles missing packages and rejected attributes without raising:

- Without the OpenTelemetry packages, the constructor logs a debug message and all methods do nothing.
- Without the OTLP exporter packages, the constructor logs a warning. Spans are still created but not exported.
- If the span rejects the cost attributes in `record_cost()` (e.g., because the span has ended), a debug message is logged and no exception is raised.

## Integration Example

The following example opens the spans manually around direct `AgentRunner` calls:

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
                    if result.cost_usd is not None:
                        telemetry.record_cost(agent_name, result.cost_usd)
```

## Related

- [Architecture Overview](../architecture/overview.md): how the observability module fits with the other modules
