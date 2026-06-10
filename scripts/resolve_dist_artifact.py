"""Resolve a single built distribution artifact from ``dist/``.

Shell globs make ``uv --with dist/*.whl`` fragile when stale artifacts remain:
the shell expands every match and uv interprets the extras as commands or
arguments. This helper prints one exact path or fails with a clear diagnostic.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PATTERNS = {
    "wheel": "*.whl",
    "sdist": "*.tar.gz",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=sorted(PATTERNS))
    parser.add_argument("--dist-dir", type=Path, default=Path("dist"))
    args = parser.parse_args(argv)

    pattern = PATTERNS[args.kind]
    artifacts = sorted(args.dist_dir.glob(pattern))
    if len(artifacts) != 1:
        found = ", ".join(str(path) for path in artifacts) if artifacts else "none"
        print(
            f"expected exactly one {args.kind} artifact matching "
            f"{args.dist_dir / pattern}; found {len(artifacts)}: {found}",
            file=sys.stderr,
        )
        return 1

    print(artifacts[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
