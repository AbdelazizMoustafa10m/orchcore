# Prompt Templating

The `prompt` module renders Jinja2 prompt templates, strips YAML frontmatter, and looks up templates across several directories. Agent prompts often include project names, file lists, earlier phase outputs, or mode-specific instructions, and the module fills them in with Jinja2 variables, conditionals, and loops.

Templates render in a sandboxed environment, so template code cannot reach arbitrary Python objects (see [Security](#security)). Only `TemplateLoader.load()` and `strip_frontmatter()` remove frontmatter; `render_template()` and `render_string()` render the text as given.

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

For templates stored in configuration or built at runtime:

```python
from orchcore.prompt import render_string

output = render_string(
    template_str="Analyze {{ project_name }} and focus on {{ focus_area }}.",
    variables={"project_name": "orchcore", "focus_area": "error handling"},
)
```

## Template Loader

`TemplateLoader` finds a template by name across several directories and returns its text with frontmatter removed. It does not render the template:

```python
from pathlib import Path
from orchcore.prompt import TemplateLoader

loader = TemplateLoader(template_dirs=[
    Path("prompts/custom"),    # Project-specific templates (checked first)
    Path("prompts/defaults"),  # Fallback templates
])

# Searches for: planning, planning.md, planning.j2, planning.txt
content = loader.load("planning")  # raises FileNotFoundError if no file matches

# Check existence without loading
if loader.exists("review"):
    review_prompt = loader.load("review")
```

The directories are searched in the order given. In each directory, the loader tries the exact name first, then the name with `.md`, `.j2`, and `.txt` appended. Names that would resolve outside a template directory (e.g., `../secrets`) are rejected.

## Frontmatter Stripping

Templates can include YAML frontmatter (delimited by `---`) for metadata:

```markdown
---
description: Planning phase prompt
author: team
---

# Planning Phase

Analyze the codebase for {{ project_name }}...
```

!!! warning "Do not pass a file with frontmatter to `render_template()`"
    `render_template()` and `render_string()` do not strip frontmatter, so it ends up in the rendered prompt. Strip it first with `TemplateLoader.load()` or `strip_frontmatter()`, then render the result with `render_string()`.

```python
from pathlib import Path
from orchcore.prompt import render_string, strip_frontmatter

# Option 1: TemplateLoader strips the frontmatter
template = loader.load("planning")

# Option 2: strip it yourself
template = strip_frontmatter(Path("prompts/planning.md").read_text(encoding="utf-8"))

prompt = render_string(template_str=template, variables={"project_name": "orchcore"})
```

## Template Path Resolution

`resolve_template_path()` resolves a configured path, which may be relative, against a base directory. If the file does not exist, it logs a warning and returns `None`, so the caller can fall back to a built-in template:

```python
from orchcore.prompt import resolve_template_path

path = resolve_template_path(
    configured_path=Path("prompts/planning.md"),
    base_dir=Path("/project/root"),
    fallback_name="planning prompt",
)
# Returns the resolved absolute Path, or None if the path is None or the file is missing
```

## Security

Templates are rendered in a Jinja2 `SandboxedEnvironment`, which blocks template code from accessing unsafe attributes and arbitrary Python objects. `StrictUndefined` is enabled, so a reference to an undefined variable raises `jinja2.UndefinedError` instead of rendering as an empty string.

## Related

- [Architecture Overview](../architecture/overview.md): how the prompt module fits with the other modules
- [Quick Start](../getting-started/quickstart.md): an end-to-end pipeline example
