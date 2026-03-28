# Installation

## Requirements

- **Python 3.12 or later** — orchcore uses `TaskGroup`, `tomllib`, and modern type syntax
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

orchcore keeps its core dependency footprint minimal. Optional extras add display framework support:

| Extra | Install Command | What It Adds |
|-------|----------------|--------------|
| `rich` | `uv pip install orchcore[rich]` | [Rich](https://rich.readthedocs.io/) >= 13.0 for styled terminal output |
| `tui` | `uv pip install orchcore[tui]` | [Textual](https://textual.textualize.io/) >= 0.40 for TUI dashboards |
| `dev` | `uv pip install orchcore[dev]` | mypy, pytest, pytest-asyncio, hypothesis, ruff, coverage |

## Core Dependencies

orchcore has only three core dependencies:

| Package | Version | Purpose |
|---------|---------|---------|
| [pydantic](https://docs.pydantic.dev/) | >= 2.10 | Data validation and typed models |
| [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) | >= 2.7 | Layered configuration with TOML support |
| [Jinja2](https://jinja.palletsprojects.com/) | >= 3.1 | Prompt template rendering |

## Verify Installation

```bash
python -c "import orchcore; print(orchcore.__version__)"
```

## Agent CLI Prerequisites

orchcore orchestrates external agent CLIs as subprocesses. You need at least one agent CLI installed and on your `PATH`:

| Agent | Install | Docs |
|-------|---------|------|
| Claude Code | `npm install -g @anthropic-ai/claude-code` | [claude.ai/code](https://claude.ai/code) |
| Codex | `npm install -g @openai/codex` | [OpenAI Codex](https://github.com/openai/codex) |
| Gemini CLI | `npm install -g @anthropic-ai/gemini-cli` | [Gemini CLI](https://github.com/google-gemini/gemini-cli) |

orchcore itself does not handle API keys — each agent CLI manages its own authentication via environment variables (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc.).

## Next Steps

- [Quick Start](quickstart.md) — define agents, build phases, run your first pipeline
- [Configuration Reference](../reference/configuration.md) — customize settings via TOML, env vars, or profiles
