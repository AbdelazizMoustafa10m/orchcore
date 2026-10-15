# Installation

## Requirements

- Python 3.12 or later. orchcore uses `tomllib`, modern type syntax, and current asyncio APIs.
- uv or pip for package management. The commands below use uv, as does the project's own tooling.

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

This installs orchcore in editable mode with the `dev` extra (see below).

## Optional Extras

The core install has four dependencies (see [Core Dependencies](#core-dependencies)). Optional extras add display, observability, and development tooling. The quotes in the install commands stop shells such as zsh from expanding the brackets.

| Extra | Install Command | What It Adds |
|-------|----------------|--------------|
| `rich` | `uv pip install "orchcore[rich]"` | [Rich](https://rich.readthedocs.io/) >= 13.0 for styled terminal output |
| `tui` | `uv pip install "orchcore[tui]"` | [Textual](https://textual.textualize.io/) >= 0.40 for TUI dashboards |
| `telemetry` | `uv pip install "orchcore[telemetry]"` | OpenTelemetry API and SDK with the OTLP gRPC and HTTP exporters |
| `dev` | `uv pip install "orchcore[dev]"` | commitizen, coverage, hypothesis, mypy, pre-commit, pytest, pytest-asyncio, pytest-cov, pyright, ruff |

## Core Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| [pydantic](https://docs.pydantic.dev/) | >= 2.10 | Data validation and typed models |
| [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) | >= 2.7 | Layered configuration with TOML support |
| [Jinja2](https://jinja.palletsprojects.com/) | >= 3.1 | Prompt template rendering |
| [tzdata](https://pypi.org/project/tzdata/) | >= 2024.1 | Timezone database fallback for named reset times on Windows and slim containers |

## Type Hints (PEP 561)

The package includes a `py.typed` marker file (PEP 561), so mypy and other type checkers read orchcore's inline type annotations. No stub packages are needed.

## Verify Installation

```bash
python -c "import orchcore; print(orchcore.__version__)"
```

## Agent CLI Prerequisites

orchcore runs external agent CLIs as subprocesses, so at least one agent CLI must be installed and on your `PATH`:

| Agent | Install | Docs |
|-------|---------|------|
| Claude Code | `npm install -g @anthropic-ai/claude-code` | [claude.ai/code](https://claude.ai/code) |
| Codex | `npm install -g @openai/codex` | [OpenAI Codex](https://developers.openai.com/codex/cli) |
| Antigravity CLI (`agy`) | `curl -fsSL https://antigravity.google/cli/install.sh \| bash` | [Antigravity CLI](https://antigravity.google/docs/cli/install) |

orchcore does not handle agent authentication. Each agent CLI manages its own login or API-key flow. By default, orchcore launches agent subprocesses with a filtered environment that removes inherited provider variables such as `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, and `GEMINI_API_KEY`. To pass them through, list them in `env_passlist`, set `env_policy = "inherit"`, or supply explicit values through `env_vars` (see [Environment Policy](../guides/agent-registry.md#environment-policy)).

## Next Steps

- [Quick Start](quickstart.md): define agents, build phases, and run your first pipeline.
- [Configuration Reference](../reference/configuration.md): set options through TOML files, environment variables, or profiles.
