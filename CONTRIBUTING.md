# Contributing to orchcore

Thank you for your interest in contributing to orchcore!

For the full contributing guide covering development setup, commands, testing, code standards,
and pull request expectations, see
[doc/development/contributing.md](doc/development/contributing.md).

## Quick Start

```bash
git clone https://github.com/AbdelazizMoustafa10m/orchcore.git
cd orchcore
uv pip install -e ".[dev]"
make check  # lint + typecheck + test; must pass before submitting
```

## Commit Convention

This project uses [Conventional Commits](https://www.conventionalcommits.org/). Pull request
titles are validated automatically in CI.

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md).

## Security

Please report security issues privately as described in [SECURITY.md](SECURITY.md).
