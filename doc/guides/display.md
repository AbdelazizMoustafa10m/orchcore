# Display Utilities

The `display` module provides colored log functions and value formatters for terminal output. It uses plain ANSI escape codes and has no dependencies, so a consuming project can print colored status lines without installing Rich or a TUI framework. All output goes to stderr, which keeps stdout free for agent output that other tools may read.

## Logging Functions

Each log function writes one line to stderr, prefixed with a UTC timestamp (`HH:MM:SS`):

```python
from orchcore.display import log_info, log_success, log_warn, log_error, log_dim

log_info("Starting pipeline with 3 phases")     # cyan  ">" icon
log_success("Phase planning completed")          # green "+" icon
log_warn("Agent stalled for 300 seconds")        # yellow "!" icon
log_error("Agent claude failed with exit code 1") # red   "x" icon
log_dim("Skipping optional cleanup step")         # dim, no icon
```

Output looks like:

```
14:30:00 > Starting pipeline with 3 phases
14:30:45 + Phase planning completed
14:35:00 ! Agent stalled for 300 seconds
14:36:00 x Agent claude failed with exit code 1
```

## Status Line

`status_line()` writes a progress line that starts with a carriage return (`\r`), so each call overwrites the previous one:

```python
from orchcore.display import status_line, clear_status_line

# Updates in place on a single line
status_line(elapsed=125.0, tool_count=12, cost=0.42, state="WRITING")
# Output: # 2m05s | 12 tools | $0.42 | WRITING

# Clear when done
clear_status_line()
```

## Phase Header

`phase_header()` renders a section divider for phase transitions:

```python
from orchcore.display import phase_header

phase_header("execution", index=1, total=3)
```

Output:

```
============================================================
  Phase 2/3: execution
============================================================
```

## Summary Box

`summary_box()` renders a bordered key-value summary:

```python
from orchcore.display import summary_box

summary_box("Pipeline Complete", {
    "Status": "Success",
    "Duration": "4m 32s",
    "Total Cost": "$1.23",
    "Phases": "3/3 passed",
})
```

## Formatting Helpers

The formatters take typed values (`timedelta`, `Decimal`, a token-count dict, a byte count) and return display strings. `format_duration()`, `format_cost()`, and `format_tokens()` return a placeholder for `None`:

```python
from datetime import timedelta
from decimal import Decimal
from orchcore.display import format_cost, format_duration, format_tokens, format_file_size

format_duration(timedelta(minutes=4, seconds=32))   # "4m 32s"
format_duration(timedelta(seconds=8))                # "8s"
format_duration(None)                                # "—"

format_cost(Decimal("1.2345"))                       # "$1.2345"
format_cost(None)                                    # "N/A"

format_tokens({"input_tokens": 15000, "output_tokens": 3200})  # "15,000/3,200"
format_tokens(None)                                             # "—"

format_file_size(2_500_000)                          # "2.4 MB"
format_file_size(512)                                # "512 bytes"
```

The ANSI color constants (`RED`, `CYAN`, `BOLD`, and so on) are defined in `orchcore.display.logging` but are not exported by `orchcore.display`. Code that needs them imports them from the submodule: `from orchcore.display.logging import CYAN, NC`.

## Color Output

Colors are emitted only when stderr is a terminal, so redirected logs contain no escape sequences. Setting `FORCE_COLOR` (to any non-empty value) turns colors on, and setting `NO_COLOR` (to any value) turns them off. `FORCE_COLOR` wins if both are set.

## Related

- [Writing a UICallback](writing-a-uicallback.md): building a display layer on the `UICallback` protocol
- [Architecture Overview](../architecture/overview.md): how the display module fits with the other modules
