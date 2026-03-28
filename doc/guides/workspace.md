# Workspace Management

orchcore's `WorkspaceManager` handles the lifecycle of pipeline execution artifacts — active workspace directories, file I/O, archival with gzip compression, and cleanup.

## Overview

Each pipeline run produces output files (agent markdown, stream logs, stderr logs) that need a consistent directory structure. `WorkspaceManager` provides:

- **Active workspace** — a working directory for in-progress pipeline output
- **Archival** — timestamped archive copies with `.stream` file compression
- **Cleanup** — automatic removal of the active workspace on success

## Basic Usage

```python
from pathlib import Path
from orchcore.workspace import WorkspaceManager

ws = WorkspaceManager(
    project_root=Path("."),
    workspace_name=".orchcore-workspace",  # default
)
ws.set_task_slug("implement user authentication")
ws.ensure_dirs()

# write_file writes directly under workspace_dir — it does NOT create
# parent directories, so the name must be a flat filename.
ws.write_file("plan.md", "# Plan\n...")
content = ws.read_file("plan.md")

# Archive when done
archive_path = ws.archive()
print(f"Archived to: {archive_path}")
```

**Note:** `write_file` and `read_file` operate on flat filenames inside `workspace_dir`. The nested `outputs/<phase>/<agent>.md` directory structure is created by `PhaseRunner`, which calls `mkdir(parents=True)` before writing agent output. If you need subdirectories for manual writes, create them yourself first.

## Context Manager

`WorkspaceManager` supports the context manager protocol. On successful exit, the active workspace is cleaned up automatically. On error, it is preserved for debugging:

```python
with WorkspaceManager(project_root=Path(".")) as ws:
    ws.set_task_slug("fix login bug")
    ws.ensure_dirs()
    # ... run pipeline ...
    ws.archive()
# workspace_dir is removed on success, preserved on exception
```

## Directory Structure

```
project_root/
├── .orchcore-workspace/                 # Active workspace (created by ensure_dirs)
│   └── outputs/                         # Created by PhaseRunner
│       ├── planning/
│       │   └── claude.md
│       └── execution/
│           ├── claude.md
│           ├── claude.stream            # Raw JSONL stream
│           └── claude.log               # stderr
└── reports/runs/                        # Archive root
    ├── 2026-03-28_14-30-00_implement-user-auth/
    │   └── outputs/
    │       ├── planning/
    │       │   └── claude.md
    │       └── execution/
    │           ├── claude.md
    │           ├── claude.stream.gz     # Compressed
    │           └── claude.log
    └── latest -> 2026-03-28_14-30-00_implement-user-auth
```

## Constructor Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `project_root` | `Path` | required | Root directory of the project |
| `workspace_name` | `str` | `".orchcore-workspace"` | Name of the active workspace directory |
| `reports_dir` | `Path \| None` | `None` (→ `project_root/reports`) | Base directory for archives. Relative paths are resolved against `project_root` |
| `archive_subdir` | `str` | `"runs"` | Subdirectory under `reports_dir` for archived runs |

## Task Slug

`set_task_slug()` generates a filesystem-safe slug from a task description (first 40 chars, lowercased, non-alphanumeric replaced with hyphens). This slug is appended to the archive timestamp:

```python
ws.set_task_slug("Fix the login page CSS overflow issue")
# Archive dir: reports/runs/2026-03-28_14-30-00_fix-the-login-page-css-overflow-issue
```

## Archival

`archive()` copies the workspace to the archive directory:

- `.stream` files are compressed with gzip (stored as `.stream.gz`)
- All other files are copied uncompressed
- A `latest` symlink is created pointing to the newest archive
- `.stream` files are removed from the active workspace after archival
- Collision-safe: appends `-1`, `-2`, etc. if the archive directory already exists

## Async Variants

For use within async pipelines, async wrappers are provided:

```python
content = await ws.aread_file("plan.md")
path = await ws.awrite_file("summary.md", output)
archive_path = await ws.aarchive()
```

## Fresh vs. Reuse Mode

`ensure_dirs(reuse=False)` (default) wipes any existing workspace before creating fresh directories. `ensure_dirs(reuse=True)` preserves existing contents and only creates directories that don't exist — useful for resumed pipeline runs.

## Related

- [Architecture Overview](../architecture/overview.md) — how workspace fits into the broader system
