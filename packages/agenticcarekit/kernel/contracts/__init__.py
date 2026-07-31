"""The five frozen contracts (Phase 0). Everything else in the toolkit is
built against — and only against — what is exported here.

Contract 1: Capabilities / Provider   (provider.py)
Contract 2: Sensitive / PolicyContext (policy.py)
Contract 3: @tool                     (tools.py)
Contract 4: TraceEvent                (trace.py)
Contract 5: ack.toml                  (config.py)

Changing a contract means amending it here AND in ``spec/schemas/`` AND in
``docs/CONTRACTS.md`` — never patching around it downstream.
"""

from .config import AckConfig, ModelRef
from .errors import AckError, CapabilityMismatch, PolicyViolation, error_registry, explain
from .policy import PolicyContext, Redaction, Redactor, Sensitive
from .provider import (
    VISION_TOKEN_BUDGETS,
    AudioPart,
    Capabilities,
    Chunk,
    EgressClass,
    GenerateRequest,
    GenerateResponse,
    ImageDetail,
    ImagePart,
    Message,
    Modality,
    Part,
    Provider,
    Role,
    TextPart,
    ToolCall,
    Usage,
)
from .tools import Permission, Tool, ToolSpec, tool
from .trace import EventKind, TraceEvent

__all__ = [
    "AckConfig",
    "AckError",
    "AudioPart",
    "Capabilities",
    "CapabilityMismatch",
    "Chunk",
    "EgressClass",
    "EventKind",
    "GenerateRequest",
    "GenerateResponse",
    "ImageDetail",
    "ImagePart",
    "Message",
    "Modality",
    "ModelRef",
    "Part",
    "Permission",
    "PolicyContext",
    "PolicyViolation",
    "Provider",
    "Redaction",
    "Redactor",
    "Role",
    "Sensitive",
    "TextPart",
    "Tool",
    "ToolCall",
    "ToolSpec",
    "TraceEvent",
    "Usage",
    "VISION_TOKEN_BUDGETS",
    "error_registry",
    "explain",
    "tool",
]
