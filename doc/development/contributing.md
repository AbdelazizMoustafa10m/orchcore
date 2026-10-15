# Contributing

## Development Setup

```bash
git clone https://github.com/AbdelazizMoustafa10m/orchcore.git
cd orchcore
uv pip install -e ".[dev]"
```

This installs orchcore in editable mode with the `dev` extra: commitizen, coverage,
hypothesis, mypy, pre-commit, pytest, pytest-asyncio, pytest-cov, pyright, and ruff.

### Working from a non-git checkout

orchcore derives its version from git tags through hatch-vcs. Source exports, distribution
packaging trees, and other checkouts without `.git` need an explicit pretend version before
an editable install or `uv sync` can resolve the project:

```bash
export SETUPTOOLS_SCM_PRETEND_VERSION_FOR_ORCHCORE=0.0.0.dev0
uv sync --extra dev
```

PowerShell:

```powershell
$env:SETUPTOOLS_SCM_PRETEND_VERSION_FOR_ORCHCORE = "0.0.0.dev0"
uv sync --extra dev
```

The `_FOR_ORCHCORE` suffix limits the override to this package. `make install-nogit` runs
the same steps in a POSIX shell.

## Commands

```bash
make check         # Run all checks: lint + typecheck + test
make lint          # ruff check + ruff format --check + README example check
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

Tests use `asyncio_mode = "auto"`, so async test functions need no `@pytest.mark.asyncio`
decorator. Property-based tests use Hypothesis and run as part of the normal `pytest` run.
Warnings are treated as errors (`filterwarnings = ["error"]`).

### Test Organization

```
tests/
├── conftest.py              # Shared fixtures (sample configs, JSONL data)
├── fixtures/                # mock_agent.py for integration tests
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

mypy runs in strict mode over the source, the tests, and the examples:

```bash
mypy src/ tests/ examples/ --strict
```

All public functions need type annotations. The Pydantic mypy plugin is enabled, so model
fields and constructors are type-checked. CI also runs `pyright --verifytypes orchcore` to
check that the public API is fully typed (`make verifytypes` runs it locally).

### Linting

ruff runs with the following rule sets and a 100-character line length (configured in
`pyproject.toml`):

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

CI requires at least 95% total coverage. `scripts/check_coverage_thresholds.py` also
requires 95% or more for `recovery/rate_limit.py` and 85% or more for `pipeline/engine.py`:

```toml
[tool.coverage.report]
fail_under = 95
```

### Module Exports

Each package lists its public API in `__all__` in its `__init__.py`.

## Documentation

Documentation lives in `doc/` and is built with [Zensical](https://zensical.org/):

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

1. Fork the repository and create a feature branch.
2. Make your changes, and add or update tests for any changed behavior.
3. Run `make check`. All checks must pass.
4. Open a pull request with a title that follows [Conventional Commits](https://www.conventionalcommits.org/) (e.g., `fix(stream): handle empty Codex deltas`). CI validates the title, and release-please builds the changelog from the commit messages on `main`.

## Architecture Decision Records

Design decisions are recorded as ADRs in `doc/architecture/adrs/`. For a significant architectural change, add a new ADR in the MADR format of the existing ones, and add it to the `nav` in `zensical.toml` and to the ADR table in `doc/index.md`.
