# Installation

## Requirements

- **Python 3.12 or later** — orchcore uses `tomllib`, modern type syntax, and current asyncio APIs
- **uv** (recommended) or pip for package management

## Install from PyPI

=== "uv (recommended)"

    ```bash
    uv pip install orchcore
    ```

=== "pip"

    ```bash
    pip install orchcore
    ```

## Install from Source

```bash
git clone https://github.com/AbdelazizMoustafa10m/orchcore.git
cd orchcore
uv pip install -e ".[dev]"
```

This installs orchcore in editable mode with all development dependencies (mypy, pytest,
pytest-asyncio, hypothesis, ruff, coverage).

## Optional Extras

orchcore keeps its core dependency footprint minimal. Optional extras add display and observability support:

| Extra | Install Command | What It Adds |
|-------|----------------|--------------|
| `rich` | `uv pip install orchcore[rich]` | [Rich](https://rich.readthedocs.io/) >= 13.0 for styled terminal output |
| `tui` | `uv pip install orchcore[tui]` | [Textual](https://textual.textualize.io/) >= 0.40 for TUI dashboards |
| `telemetry` | `uv pip install orchcore[telemetry]` | OpenTelemetry tracing support with OTLP gRPC and HTTP exporters |
| `dev` | `uv pip install orchcore[dev]` | mypy, pytest, pytest-asyncio, hypothesis, ruff, coverage |

## Core Dependencies

orchcore has four core dependencies:

| Package | Version | Purpose |
|---------|---------|---------|
| [pydantic](https://docs.pydantic.dev/) | >= 2.10 | Data validation and typed models |
| [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) | >= 2.7 | Layered configuration with TOML support |
| [Jinja2](https://jinja.palletsprojects.com/) | >= 3.1 | Prompt template rendering |
| [tzdata](https://pypi.org/project/tzdata/) | >= 2024.1 | Timezone database fallback for named reset times on Windows and slim containers |

## Type Hints (PEP 561)

orchcore is PEP 561 compliant — the package includes a `py.typed` marker file. When you install orchcore, mypy and other type checkers automatically discover its inline type annotations. No stub packages are needed.

## Verify Installation

```bash
python -c "import orchcore; print(orchcore.__version__)"
```

## Agent CLI Prerequisites

orchcore orchestrates external agent CLIs as subprocesses. You need at least one agent CLI installed and on your `PATH`:

| Agent | Install | Docs |
|-------|---------|------|
| Claude Code | `npm install -g @anthropic-ai/claude-code` | [claude.ai/code](https://claude.ai/code) |
| Codex | `npm install -g @openai/codex` | [OpenAI Codex](https://developers.openai.com/codex/cli) |
| Gemini CLI | `npm install -g @google/gemini-cli` | [Gemini CLI](https://geminicli.com/docs/) |

orchcore itself does not handle agent authentication — each agent CLI manages its own login or API-key flow. By default, orchcore launches agent subprocesses with a filtered environment, so inherited provider variables such as `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `GEMINI_API_KEY` may be omitted unless you passlist them with `env_passlist`, use `env_policy = "inherit"`, or supply explicit values through `env_vars`.

## Next Steps

- [Quick Start](quickstart.md) — define agents, build phases, run your first pipeline
- [Configuration Reference](../reference/configuration.md) — customize settings via TOML, env vars, or profiles
