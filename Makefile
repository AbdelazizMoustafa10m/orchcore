PYTHON_SOURCES := src tests examples scripts

.PHONY: install install-nogit lint typecheck test check clean verifytypes smoke-dist readme-example

install:
	uv pip install -e ".[dev]"

install-nogit:
	# For source exports without .git, hatch-vcs honors this setuptools-scm override.
	SETUPTOOLS_SCM_PRETEND_VERSION_FOR_ORCHCORE=0.0.0.dev0 uv sync --extra dev

lint:
	ruff check $(PYTHON_SOURCES)
	ruff format --check $(PYTHON_SOURCES)
	python scripts/check_readme_example.py

format:
	ruff format $(PYTHON_SOURCES)
	ruff check --fix $(PYTHON_SOURCES)
	python scripts/check_readme_example.py --fix

typecheck:
	mypy src/ tests/ examples/ --strict

test:
	pytest tests/ -v

check: lint typecheck test
	@echo "All checks passed."

verifytypes:
	uv run pyright --verifytypes orchcore --ignoreexternal

smoke-dist:
	rm -rf dist/
	uv build
	uv run --isolated --no-cache --no-project --with "$$(uv run python scripts/resolve_dist_artifact.py wheel)" scripts/run_smoke_test.py
	uv run --isolated --no-cache --no-project --with "$$(uv run python scripts/resolve_dist_artifact.py sdist)" scripts/run_smoke_test.py

readme-example:
	python scripts/check_readme_example.py

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .mypy_cache -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	rm -rf dist/ build/ *.egg-info
