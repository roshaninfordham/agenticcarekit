/**
 * Capabilities — the honest-minimal half of the TypeScript port.
 *
 * Two capabilities are ported: `extract` (schema-validated structured
 * output with exactly one repair retry) and `agents` (a tool-calling loop
 * with a step budget and cancellation). They are unit-tested, not
 * conformance-verified, because the corpus does not cover them.
 *
 * `voice` and `rag` are NOT ported. See `README.md` for the support matrix;
 * claiming broader support than has been tested is an anti-goal
 * (`docs/brief.md` §13).
 */

export { ExtractOptions, extract } from "./extract.js";
export {
  AgentLoop,
  AgentLoopOptions,
  AgentResult,
  RunOptions,
  StepRecord,
  StoppedReason,
} from "./agents.js";
