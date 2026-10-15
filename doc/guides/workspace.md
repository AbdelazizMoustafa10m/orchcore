# Workspace Management

`WorkspaceManager` manages the files a pipeline run produces: agent output (Markdown), raw JSONL streams, and stderr logs. It creates the active workspace directory, reads and writes files inside it, archives finished runs with gzip-compressed streams, and removes the active workspace after a successful run.

The **active workspace** holds the output of the run in progress. An **archive** is a timestamped copy of a finished run under the reports directory.

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

ws.write_file("plan.md", "# Plan\n...")
ws.write_file("notes/review.md", "# Review\n...")
content = ws.read_file("plan.md")

# Archive when done
archive_path = ws.archive()
print(f"Archived to: {archive_path}")
```

`workspace_name` and the paths passed to `write_file()` and `read_file()` must stay inside the workspace. Empty, absolute, drive-qualified, or parent-escaping names (e.g., `../plan.md`) raise `ValueError`, and `cleanup()` never deletes `project_root`. Nested names such as `notes/review.md` are allowed, and missing parent directories are created.

## Context Manager

`WorkspaceManager` is a context manager. When the `with` block exits normally, the active workspace is removed. When it exits with an exception, the workspace is kept for debugging:

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
    ├── 2026-03-28_14-30-00_implement-user-authentication/
    │   └── outputs/
    │       ├── planning/
    │       │   └── claude.md
    │       └── execution/
    │           ├── claude.md
    │           ├── claude.stream.gz     # Compressed
    │           └── claude.log
    └── latest -> 2026-03-28_14-30-00_implement-user-authentication
    # or latest.txt containing the archive directory name when symlinks are unavailable
```

## Constructor Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `project_root` | `Path` | required | Root directory of the project |
| `workspace_name` | `str` | `".orchcore-workspace"` | Name of the active workspace directory |
| `reports_dir` | `Path \| None` | `None` (→ `project_root/reports`) | Base directory for archives. Relative paths are resolved against `project_root` |
| `archive_subdir` | `str` | `"runs"` | Subdirectory under `reports_dir` for archived runs |

## Task Slug

`set_task_slug()` turns a task description into a filesystem-safe slug: it takes the first 40 characters, lowercases them, replaces each run of non-alphanumeric characters with a hyphen, and trims leading and trailing hyphens. An empty result becomes `untitled`. The slug is appended to the archive timestamp:

```python
ws.set_task_slug("Fix the login page CSS overflow issue")
# Archive dir: reports/runs/2026-03-28_14-30-00_fix-the-login-page-css-overflow-issue
```

## Archival

`archive()` copies the workspace to the archive directory:

- `.stream` files are compressed with gzip and stored as `.stream.gz`.
- All other files are copied uncompressed.
- A `latest` symlink is pointed at the newest archive. If the platform cannot create the symlink, `latest.txt` stores the newest archive directory name instead.
- `.stream` files are removed from the active workspace after archival.
- If the archive directory already exists, `-1`, `-2`, and so on are appended to the name.

Use `latest_path()` to resolve the newest archive portably; it handles both the
symlink and `latest.txt` forms.

## Async Variants

Async wrappers run the file operations in a worker thread, so they do not block the event loop:

```python
content = await ws.aread_file("plan.md")
path = await ws.awrite_file("summary.md", output)
archive_path = await ws.aarchive()
```

## Fresh vs. Reuse Mode

`ensure_dirs(reuse=False)` (the default) deletes any existing workspace before creating fresh directories. `ensure_dirs(reuse=True)` keeps existing contents and creates only the missing directories. Use it for resumed pipeline runs, which read their resume state from the existing workspace.

## Related

- [Architecture Overview](../architecture/overview.md): how the workspace module fits with the other modules
