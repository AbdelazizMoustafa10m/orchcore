# Prompt Templating

orchcore's `prompt` module provides Jinja2-based template rendering with frontmatter stripping and configurable template directories.

## Overview

Agent prompts are often parameterized — they include project names, file lists, phase outputs, or mode-specific instructions. The prompt module handles:

- **Jinja2 rendering** — variable substitution, conditionals, loops
- **Sandboxed execution** — templates cannot access arbitrary Python objects
- **Frontmatter stripping** — YAML frontmatter is removed before rendering
- **Multi-directory search** — templates are resolved across configurable directories

## Rendering a Template File

```python
from pathlib import Path
from orchcore.prompt import render_template

output = render_template(
    template_path=Path("prompts/planning.md"),
    variables={
        "project_name": "orchcore",
        "mode": "plan",
        "files": ["src/main.py", "src/utils.py"],
    },
)
```

## Rendering a String

For templates stored in configuration or generated dynamically:

```python
from orchcore.prompt import render_string

output = render_string(
    template_str="Analyze {{ project_name }} and focus on {{ focus_area }}.",
    variables={"project_name": "orchcore", "focus_area": "error handling"},
)
```

## Template Loader

`TemplateLoader` searches multiple directories for templates by name, with automatic extension resolution:

```python
from pathlib import Path
from orchcore.prompt import TemplateLoader

loader = TemplateLoader(template_dirs=[
    Path("prompts/custom"),    # Project-specific templates (checked first)
    Path("prompts/defaults"),  # Fallback templates
])

# Searches for: planning.md, planning.j2, planning.txt
content = loader.load("planning")

# Check existence without loading
if loader.exists("review"):
    review_prompt = loader.load("review")
```

**Supported extensions:** `.md`, `.j2`, `.txt`

**Search order:** Each directory is tried in order. Within each directory, the exact name is tried first, then each extension is appended.

## Frontmatter Stripping

Templates can include YAML frontmatter (delimited by `---`) for metadata. It is automatically stripped before rendering:

```markdown
---
description: Planning phase prompt
author: team
---

# Planning Phase

Analyze the codebase for {{ project_name }}...
```

The rendered output starts at `# Planning Phase` — the frontmatter block is removed.

## Template Path Resolution

`resolve_template_path()` resolves a configured path (which may be relative) against a base directory, with a warning if the file is missing:

```python
from orchcore.prompt import resolve_template_path

path = resolve_template_path(
    configured_path=Path("prompts/planning.md"),
    base_dir=Path("/project/root"),
    fallback_name="planning prompt",
)
# Returns resolved absolute Path, or None if not found
```

## Security

Templates are rendered in a `SandboxedEnvironment` that prevents template code from accessing arbitrary Python objects or executing dangerous operations. `StrictUndefined` is enabled — referencing an undefined variable raises an error rather than silently rendering an empty string.

## Related

- [Architecture Overview](../architecture/overview.md) — how prompt templating fits into the broader system
- [Quick Start](../getting-started/quickstart.md) — end-to-end pipeline example
