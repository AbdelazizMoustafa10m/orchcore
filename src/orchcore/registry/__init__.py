"""orchcore.registry -- Agent configuration and registry."""

from orchcore.registry.agent import (
    AgentConfig,
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
