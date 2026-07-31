"""agenticcarekit — the open-model stack for health AI.

Runs on your laptop. Ships with the privacy boundary built in.

The public surface re-exports the frozen contracts; concrete providers,
policy, trace, capabilities and packs live in their subpackages and are
imported explicitly (nothing is hidden — invariant 3: ejectable).

No telemetry, ever.
"""

from agenticcarekit.kernel.contracts import (  # noqa: F401
    AckConfig,
    AckError,
    Capabilities,
    CapabilityMismatch,
    Chunk,
    EgressClass,
    GenerateRequest,
    GenerateResponse,
    Message,
    Modality,
    ModelRef,
    PolicyViolation,
    Provider,
    Redaction,
    Redactor,
    Sensitive,
    ToolSpec,
    TraceEvent,
    explain,
    tool,
)

__version__ = "0.1.0"
