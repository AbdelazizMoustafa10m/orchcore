"""orchcore.registry -- Agent configuration and registry."""

from orchcore.registry.agent import (
    AgentConfig,
    AgentMode,
    OutputExtraction,
    ToolSet,
)
from orchcore.registry.registry import AgentRegistry
from orchcore.registry.versioning import (
    CompatibilityStatus,
    CompatibilityVerdict,
    IncompatibleVersionSpec,
    VersionSpecifier,
    VersionSpecifierError,
    evaluate_compatibility,
)
from orchcore.stream.events import StreamFormat

__all__ = [
    "AgentConfig",
    "AgentMode",
    "AgentRegistry",
    "CompatibilityStatus",
    "CompatibilityVerdict",
    "IncompatibleVersionSpec",
    "OutputExtraction",
    "StreamFormat",
    "ToolSet",
    "VersionSpecifier",
    "VersionSpecifierError",
    "evaluate_compatibility",
]
