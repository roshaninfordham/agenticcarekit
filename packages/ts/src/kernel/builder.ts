/**
 * The canonical message builder — every Gemma 4 quirk, applied once.
 *
 * `buildOllamaChat` is the single quirk-application point in the toolkit
 * (`docs/CONTRACTS.md` → "Canonical message build"). Its output is the exact
 * Ollama `/api/chat` payload, and the conformance corpus asserts it as
 * sorted-key JSON against hand-derived fixtures.
 *
 * The six rules, in order:
 *
 * 1. Sampling defaults `1.0 / 0.95 / 64` land in `options`; request
 *    overrides win. `maxTokens` → `options.num_predict`, `stop` →
 *    `options.stop`. Context is never sent — the model declares it.
 * 2. `think: true` prepends `<|think|>` to the system message, creating one
 *    if the conversation has none. Exactly once, at the start.
 * 3. `Message.thinking` is never serialized, for any turn. Prior-turn
 *    thought blocks in history are a silent correctness bug; keeping
 *    thinking outside `parts` makes the stripping structural.
 * 4. Images and audio go to the `images` / `audio` arrays (Ollama places
 *    them before text); multiple text parts join with `"\n\n"`.
 * 5. `ImagePart.detail` maps to `options.vision_tokens` via
 *    `VISION_TOKEN_BUDGETS`; across several images the highest preset wins.
 * 6. Raw bytes are base64-encoded; a string naming an existing file is read
 *    and encoded; any other string is assumed to be base64 already.
 */

import { statSync, readFileSync } from "node:fs";

import {
  GenerateRequest,
  MediaData,
  Message,
  VISION_TOKEN_BUDGETS,
} from "../contracts/provider.js";

/**
 * Gemma 4's known-good sampling defaults (brief §2, quirk 1). `null` on a
 * request means "use these"; a value means the caller overrode deliberately.
 */
export const GEMMA4_SAMPLING: Readonly<{ temperature: number; top_p: number; top_k: number }> = {
  temperature: 1.0,
  top_p: 0.95,
  top_k: 64,
};

/**
 * Thinking is enabled by this token at the start of the system prompt
 * (brief §2, quirk 2) — not by a sampling flag.
 */
export const THINK_TOKEN = "<|think|>";

/** Closing counterpart, emitted by the model when it thinks inline. */
const THINK_CLOSE = "<|/think|>";

/**
 * Normalise image/audio payload data to base64 (rule 6).
 *
 * Bytes are encoded; a string naming an existing file is read from disk and
 * encoded; any other string is assumed to be base64 already and passed
 * through untouched. Silently re-encoding a base64 string would corrupt it.
 *
 * @example
 * encodeMedia(new TextEncoder().encode("hi")); // "aGk="
 * encodeMedia("aGk=");                         // "aGk="
 */
export function encodeMedia(data: MediaData): string {
  if (typeof data !== "string") {
    return Buffer.from(data).toString("base64");
  }
  try {
    if (statSync(data).isFile()) {
      return readFileSync(data).toString("base64");
    }
  } catch {
    // Long base64 blobs blow past filename limits on some platforms — that
    // just means it was never a path.
  }
  return data;
}

/**
 * Separate an inline `<|think|>` block from response text.
 *
 * Ollama returns thinking in its own field for most builds, but Gemma 4 will
 * emit the block inline when the token is echoed. Either way callers get
 * `[text, thinking]` with the thought block out of the text.
 *
 * @example
 * splitThinking("<|think|>weigh it<|/think|>Answer: 4"); // ["Answer: 4", "weigh it"]
 */
export function splitThinking(
  content: string,
  thinking: string | null = null,
): [string, string | null] {
  let text = content;
  let thought = thinking;
  const start = text.indexOf(THINK_TOKEN);
  if (start !== -1) {
    const end = text.indexOf(THINK_CLOSE, start);
    let inline: string;
    if (end === -1) {
      inline = text.slice(start + THINK_TOKEN.length);
      text = text.slice(0, start);
    } else {
      inline = text.slice(start + THINK_TOKEN.length, end);
      text = text.slice(0, start) + text.slice(end + THINK_CLOSE.length);
    }
    inline = inline.trim();
    if (inline) thought = thought ? `${thought}\n${inline}` : inline;
  }
  return [text.trim(), thought];
}

/**
 * One conversation turn in Ollama's wire shape (rules 3 and 4).
 *
 * `thinking` is never written. Images and audio become their own arrays;
 * text parts join with a blank line.
 */
export function serializeMessage(msg: Message): Record<string, unknown> {
  const texts: string[] = [];
  const images: string[] = [];
  const audio: string[] = [];
  for (const part of msg.parts) {
    if (part.type === "text") texts.push(part.text);
    else if (part.type === "image") images.push(encodeMedia(part.data));
    else audio.push(encodeMedia(part.data));
  }

  const out: Record<string, unknown> = { role: msg.role, content: texts.join("\n\n") };
  // Rule 4: media arrays are what Ollama renders ahead of the text.
  if (images.length > 0) out["images"] = images;
  if (audio.length > 0) out["audio"] = audio;
  if (msg.toolCalls.length > 0) {
    out["tool_calls"] = msg.toolCalls.map((tc) => ({
      function: { name: tc.name, arguments: tc.arguments },
    }));
  }
  if (msg.role === "tool" && msg.toolCallId) {
    // Ollama identifies tool results by name, not by call id.
    out["tool_name"] = msg.toolCallId;
  }
  // Rule 3: msg.thinking is deliberately absent from `out`. Do not add it.
  return out;
}

/** Highest vision-token preset across every image in the request (rule 5). */
function visionTokens(req: GenerateRequest): number | null {
  const budgets: number[] = [];
  for (const msg of req.messages) {
    for (const part of msg.parts) {
      if (part.type === "image") {
        budgets.push(VISION_TOKEN_BUDGETS[part.detail] ?? (VISION_TOKEN_BUDGETS["default"] as number));
      }
    }
  }
  return budgets.length > 0 ? Math.max(...budgets) : null;
}

/**
 * Sampling and decode options (rules 1 and 5).
 *
 * `0` is a deliberate override, not an absent value — testing falsiness
 * instead of nullness is exactly the bug `mb-003` exists to catch.
 */
export function samplingOptions(req: GenerateRequest): Record<string, unknown> {
  const opts: Record<string, unknown> = {
    temperature: req.temperature !== null ? req.temperature : GEMMA4_SAMPLING.temperature,
    top_p: req.topP !== null ? req.topP : GEMMA4_SAMPLING.top_p,
    top_k: req.topK !== null ? req.topK : GEMMA4_SAMPLING.top_k,
  };
  if (req.maxTokens !== null) opts["num_predict"] = req.maxTokens;
  if (req.stop.length > 0) opts["stop"] = [...req.stop];
  const vision = visionTokens(req);
  if (vision !== null) opts["vision_tokens"] = vision;
  // Context length is NOT sent: the model declares its window, the runtime
  // negotiates against that declaration.
  return opts;
}

/**
 * Prepend `<|think|>` to the system prompt, exactly once (rule 2).
 *
 * @example
 * applyThink([{ role: "user", content: "hi" }])[0]; // { role: "system", content: "<|think|>" }
 */
export function applyThink(
  messages: Record<string, unknown>[],
): Record<string, unknown>[] {
  for (const m of messages) {
    if (m["role"] === "system") {
      const content = String(m["content"] ?? "");
      if (!content.startsWith(THINK_TOKEN)) m["content"] = THINK_TOKEN + content;
      return messages;
    }
  }
  return [{ role: "system", content: THINK_TOKEN }, ...messages];
}

/**
 * Build the exact Ollama `/api/chat` payload for a request.
 *
 * This is the one place Gemma 4's quirks are applied. Callers never pass
 * sampling defaults, never inject the think token, and never strip history
 * thought blocks — doing any of that at a call site is the bug this
 * function exists to prevent.
 *
 * @example
 * buildOllamaChat(new GenerateRequest({ messages: [Message.text("user", "hi")] }), "gemma4:e4b");
 * // { model: "gemma4:e4b", messages: [{ role: "user", content: "hi" }],
 * //   options: { temperature: 1, top_p: 0.95, top_k: 64 }, stream: false }
 */
export function buildOllamaChat(req: GenerateRequest, model: string): Record<string, unknown> {
  let messages = req.messages.map(serializeMessage);
  if (req.think) messages = applyThink(messages);

  const payload: Record<string, unknown> = {
    model,
    messages,
    options: samplingOptions(req),
    stream: false,
  };
  // "tools" is omitted entirely when the request has none.
  if (req.tools.length > 0) {
    payload["tools"] = req.tools.map((t) => t.asFunctionSchema());
  }
  return payload;
}
