/**
 * The kernel — the conformance-verified half of the TypeScript port.
 *
 * Everything exported here is asserted by `spec/conformance/cases/` through
 * `spec/conformance/adapters/typescript.mjs`. Behaviour that the corpus does
 * not pin does not belong in this directory.
 */

export {
  GEMMA4_SAMPLING,
  THINK_TOKEN,
  applyThink,
  buildOllamaChat,
  encodeMedia,
  samplingOptions,
  serializeMessage,
  splitThinking,
} from "./builder.js";

export {
  GEMMA4_MODELS,
  MODEL_SIZES_GB,
  UNKNOWN_LOCAL,
  audioCapableTags,
  capabilitiesFor,
  ensureSupported,
} from "./models.js";

export { Policy, PolicyDecision, PolicyOptions } from "./policy.js";

export { AckConfig, AckConfigInit, ModelRef } from "./config.js";

export { TomlParseError, parseToml } from "./toml.js";

export { JsonSchema, isValid, validate } from "./jsonschema.js";
