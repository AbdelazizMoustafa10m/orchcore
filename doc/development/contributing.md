# Contributing

## Development Setup

```bash
git clone https://github.com/AbdelazizMoustafa10m/orchcore.git
cd orchcore
uv pip install -e ".[dev]"
```

This installs orchcore in editable mode with all development dependencies: mypy, pytest,
pytest-asyncio, hypothesis, ruff, and coverage.

### Working from a non-git checkout

orchcore derives versions from git tags via hatch-vcs. Source exports, distro packaging
trees, and other checkouts without `.git` need an explicit pretend version before editable
installs or `uv sync` can resolve the project:

```bash
export SETUPTOOLS_SCM_PRETEND_VERSION_FOR_ORCHCORE=0.0.0.dev0
uv sync --extra dev
```

PowerShell:

```powershell
$env:SETUPTOOLS_SCM_PRETEND_VERSION_FOR_ORCHCORE = "0.0.0.dev0"
uv sync --extra dev
```

The distribution-specific `_FOR_ORCHCORE` variable keeps the override scoped to this
package. `make install-nogit` is the same workflow for POSIX shells.

## Commands

```bash
make check         # Run all checks: lint + typecheck + test
make lint          # ruff check + ruff format --check
make format        # ruff format + ruff check --fix
make typecheck     # mypy src/ tests/ examples/ --strict
make test          # pytest tests/ -v
make smoke-dist    # build wheel+sdist and smoke-test the installed artifacts
make clean         # Remove caches and build artifacts
```

Always run `make check` before submitting changes.

## Running Tests

```bash
# Full test suite
make test

# Single file
pytest tests/test_stream/test_parser.py -v

# Property-based parser coverage
pytest tests/test_stream/test_parser_hypothesis.py -v

# Single test
pytest tests/test_stream/test_parser.py::test_parse_line_returns_empty_for_malformed_json -v

# Keyword match
pytest -k "parser" -v
```

Tests use `asyncio_mode = "auto"` — async test functions need no `@pytest.mark.asyncio` decorator.
Property-based tests use Hypothesis and are part of the normal `pytest` run.

### Test Organization

```
tests/
├── conftest.py              # Shared fixtures (sample configs, JSONL data)
├── fixtures/                # mock_claude.sh for integration tests
├── test_stream/             # Parser, filter, monitor, stall detector
├── test_pipeline/           # Phase/pipeline engine, control flow
├── test_runner/             # Subprocess runner
├── test_registry/           # Agent registry, agent config
├── test_config/             # Settings, TOML loading, profiles
├── test_recovery/           # Rate-limit, retry, git recovery
├── test_integration/        # End-to-end with real fixtures
├── test_ui/                 # UICallback protocol
├── test_signals/            # Signal handler
├── test_workspace/          # Workspace manager
├── test_prompt/             # Jinja2 templates
├── test_display/            # Logging and formatting
└── test_observability/      # Telemetry
```

## Code Standards

### Python Version

Python 3.12+ is required. The codebase uses:

- `asyncio.create_task`, `asyncio.wait(FIRST_COMPLETED)`, and `asyncio.gather(return_exceptions=True)` for concurrent agent work
- `tomllib` from stdlib for TOML parsing
- Modern type syntax (`type` statement, `X | Y` union syntax)

### Type Checking

mypy strict mode with zero escape hatches:

```bash
mypy src/ tests/ examples/ --strict
```

All public functions require type annotations. Pydantic plugin is enabled for model validation.

### Linting

ruff with the Phase 4 rule set, 100-character line length:

| Rule Set | Purpose |
|----------|---------|
| E, W | pycodestyle errors and warnings |
| F | pyflakes |
| I | isort (import sorting) |
| UP | pyupgrade |
| B | flake8-bugbear |
| SIM | flake8-simplify |
| TC | flake8-type-checking |
| ARG | flake8-unused-arguments |
| S | flake8-bandit (security) |
| RET | flake8-return |
| TRY | tryceratops |
| PT | flake8-pytest-style |
| PTH | flake8-use-pathlib |
| PL | pylint |
| RUF | ruff-specific rules |

### Test Coverage

95% minimum coverage, enforced in CI. The coverage gate also checks that
`recovery/rate_limit.py` stays at 95% or higher and `pipeline/engine.py` stays
at 85% or higher:

```toml
[tool.coverage.report]
fail_under = 95
```

### Module Exports

All modules export via `__all__` in `__init__.py`. Public API surface is explicit.

## Documentation

Documentation lives in `doc/` and is built with [Zensical](https://github.com/AbdelazizMoustafa10m/zensical):

```bash
# Install doc dependencies
uv pip install -r doc/requirements.txt

# Build docs
zensical build

# Serve locally
zensical serve
```

Configuration is in `zensical.toml` at the repo root.

## Pull Request Guidelines

1. Fork the repo and create a feature branch
2. Make your changes
3. Run `make check` — all checks must pass
4. Write or update tests for changed behavior
5. Open a pull request with a clear description

## Architecture Decision Records

Design decisions are documented as ADRs in `doc/architecture/adrs/`. When proposing a significant architectural change, add a new ADR following the existing MADR format.
