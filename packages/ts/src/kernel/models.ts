/**
 * The Gemma 4 model table — declared capabilities, never inferred.
 *
 * Every fact here comes from `docs/brief.md` §2 and mirrors the canonical
 * Python table (`kernel/providers/models.py`) entry for entry. Nothing is
 * extrapolated: where §2 is silent the entry carries the same
 * `TODO(verify)` marker rather than an invented number.
 *
 * The table is what turns "audio is E2B/E4B only" from a doc footnote into a
 * startup error with a fix attached (invariant 2).
 */

import { CapabilityMismatch } from "../contracts/errors.js";
import {
  Capabilities,
  EgressClass,
  GenerateRequest,
  Modality,
} from "../contracts/provider.js";

const TEXT_IMAGE_AUDIO: Modality[] = [Modality.TEXT, Modality.IMAGE, Modality.AUDIO];
const TEXT_IMAGE: Modality[] = [Modality.TEXT, Modality.IMAGE];
/**
 * Output is text only on every Gemma 4 variant — there is no native speech
 * output (brief §2). Voice output is a separate provider.
 */
const TEXT_ONLY: Modality[] = [Modality.TEXT];

const CTX_128K = 131_072;
const CTX_256K = 262_144;

/**
 * Build a Gemma 4 capability record. Every tag declares text-only output,
 * native function calling, streaming and thinking (brief §2, quirks 2/6/7);
 * only input modalities, context window and egress class differ.
 */
function caps(
  modalitiesIn: Modality[],
  contextTokens: number,
  egress: EgressClass = EgressClass.DEVICE,
): Capabilities {
  return new Capabilities({
    modalitiesIn,
    modalitiesOut: TEXT_ONLY,
    toolCalling: true,
    streaming: true,
    contextTokens,
    thinking: true,
    egress,
  });
}

/**
 * Tag → declared capabilities. `-mlx` variants (Apple Silicon) mirror their
 * base tag exactly; `-cloud` tags are hosted, so their egress class is
 * `public-cloud` — that single field is what the privacy boundary reads.
 */
export const GEMMA4_MODELS: Readonly<Record<string, Capabilities>> = {
  // E2B / E4B — the only tags with native audio input.
  "gemma4:e2b": caps(TEXT_IMAGE_AUDIO, CTX_128K),
  "gemma4:e4b": caps(TEXT_IMAGE_AUDIO, CTX_128K),
  "gemma4:e2b-mlx": caps(TEXT_IMAGE_AUDIO, CTX_128K),
  "gemma4:e4b-mlx": caps(TEXT_IMAGE_AUDIO, CTX_128K),
  // Larger tags — text + image, 256K context.
  "gemma4:12b": caps(TEXT_IMAGE, CTX_256K),
  "gemma4:26b": caps(TEXT_IMAGE, CTX_256K),
  "gemma4:31b": caps(TEXT_IMAGE, CTX_256K),
  // Hosted tags — no download, and egress leaves the machine.
  // TODO(verify): brief §2 states `gemma4:cloud` is hosted but not which
  // weights back it; the entry below mirrors the dense 31b it is served
  // alongside. Confirm against the registry before claiming otherwise.
  "gemma4:cloud": caps(TEXT_IMAGE, CTX_256K, EgressClass.PUBLIC_CLOUD),
  "gemma4:31b-cloud": caps(TEXT_IMAGE, CTX_256K, EgressClass.PUBLIC_CLOUD),
};

/**
 * On-disk size in GB (brief §2). Hosted `-cloud` tags are deliberately
 * absent: a missing entry means "nothing to download".
 * TODO(verify): §2 gives no separate size for `-mlx` builds; they mirror
 * their base tag here.
 */
export const MODEL_SIZES_GB: Readonly<Record<string, number>> = {
  "gemma4:e2b": 7.2,
  "gemma4:e4b": 9.6,
  "gemma4:e2b-mlx": 7.2,
  "gemma4:e4b-mlx": 9.6,
  "gemma4:12b": 7.6,
  "gemma4:26b": 18.0,
  "gemma4:31b": 20.0,
};

/**
 * Deliberately conservative fallback for tags that are not in the table.
 * Declaring less than a model can do costs a loud, fixable error; declaring
 * more costs a silent wrong answer (invariant 2).
 */
export const UNKNOWN_LOCAL = new Capabilities({
  modalitiesIn: TEXT_ONLY,
  modalitiesOut: TEXT_ONLY,
  toolCalling: true,
  streaming: true,
  contextTokens: 8_192,
  thinking: false,
  egress: EgressClass.DEVICE,
});

/**
 * Sorted tags that declare native audio input. This list is the payload of
 * every E203 audio error — the point of the error is that it names the
 * models that would work.
 */
export function audioCapableTags(): string[] {
  return Object.entries(GEMMA4_MODELS)
    .filter(([, c]) => c.modalitiesIn.has(Modality.AUDIO))
    .map(([tag]) => tag)
    .sort();
}

/** Declared capabilities for a model tag; unknown tags get `UNKNOWN_LOCAL`. */
export function capabilitiesFor(tag: string, fallback?: Capabilities): Capabilities {
  return GEMMA4_MODELS[tag] ?? fallback ?? UNKNOWN_LOCAL;
}

/**
 * Map capability gaps to the registered error code that names them.
 * Codes come from `spec/errors.json`: E203 input modality, E204 output
 * modality, E202 tool calling, E201 context window.
 */
function pickCode(gaps: string[]): string {
  if (gaps.some((g) => g.endsWith(" input"))) return "E203";
  if (gaps.some((g) => g.endsWith(" output"))) return "E204";
  if (gaps.includes("tool calling")) return "E202";
  if (gaps.some((g) => g.startsWith("context window"))) return "E201";
  return "E200";
}

/**
 * Pre-network capability check. Throws before a byte is sent.
 *
 * Compares what the request needs (input modalities; tool calling when
 * tools are attached) against what the model *declares*. A gap is a
 * `CapabilityMismatch` naming the model, the gaps, and the tags that would
 * work — never a silent degrade (invariant 2).
 */
export function ensureSupported(
  model: string,
  capabilities: Capabilities,
  req: GenerateRequest,
): void {
  const gaps = capabilities.missing({
    modalitiesIn: req.requiredModalities(),
    toolCalling: req.tools.length > 0,
  });
  if (gaps.length === 0) return;

  const candidates = audioCapableTags();
  const code = pickCode(gaps);
  const declared = [...capabilities.modalitiesIn].sort().join(", ");
  let why = `the request needs ${gaps.join(", ")}; ${model} declares ${declared} input.`;
  if (gaps.includes("audio input")) {
    why = `Native audio input is available on E2B and E4B only: ${candidates.join(", ")}`;
  }
  throw new CapabilityMismatch(`${model} does not support ${gaps.join(", ")}`, {
    code,
    missing: gaps,
    candidates,
    why,
    fix: "ack init --model gemma4:e4b-mlx",
  });
}
