/**
 * The five frozen contracts, TypeScript side.
 *
 * Everything in the TS port is built against — and only against — what is
 * exported here, mirroring `agenticcarekit.kernel.contracts`. When a
 * contract does not fit, the fix is to amend it in `docs/CONTRACTS.md`
 * (code + schema + doc, one commit), never to patch around it downstream.
 */

// Contract 1
export {
  AudioPart,
  Capabilities,
  CapabilitiesInit,
  CapabilityRequirements,
  Chunk,
  EGRESS_CLASSES,
  EgressClass,
  GenerateRequest,
  GenerateRequestInit,
  GenerateResponse,
  ImageDetail,
  ImagePart,
  MODALITIES,
  MediaData,
  Message,
  MessageInit,
  Modality,
  Part,
  Provider,
  Role,
  TextPart,
  ToolCall,
  ToolDeclaration,
  Usage,
  VISION_TOKEN_BUDGETS,
  audioPart,
  imagePart,
  isEgressClass,
  isModality,
  textPart,
  usage,
} from "./provider.js";

// Contract 2
export {
  PolicyContext,
  Redaction,
  Redactor,
  Sensitive,
  isSensitive,
  redaction,
} from "./policy.js";

// Contract 3
export {
  Permission,
  Tool,
  ToolFn,
  ToolOptions,
  ToolSpec,
  ToolSpecInit,
  VALID_PERMISSIONS,
  tool,
} from "./tools.js";

// Contract 4
export {
  EVENT_KINDS,
  EventKind,
  TraceEvent,
  TraceEventInit,
  bytesEgressed,
  canonicalJson,
} from "./trace.js";

// Errors (cross-cutting)
export {
  AckError,
  AckErrorOptions,
  CapabilityMismatch,
  CapabilityMismatchOptions,
  ErrorEntry,
  PolicyViolation,
  PolicyViolationOptions,
  errorRegistry,
  explain,
} from "./errors.js";
