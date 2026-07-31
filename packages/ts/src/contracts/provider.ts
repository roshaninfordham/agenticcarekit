/**
 * Contract 1 — `Capabilities` and the `Provider` interface.
 *
 * Providers declare what they can do; the runtime negotiates. Capability
 * negotiation turns "audio is E2B/E4B only" from a doc footnote into a
 * startup error with a fix attached (invariant 2: never silently degrade).
 *
 * Nothing here hides the provider — concrete providers expose their raw
 * client by convention (`client`), and the kernel stays callable directly.
 */

/** An input or output modality a model can consume or produce. */
export const Modality = {
  TEXT: "text",
  IMAGE: "image",
  AUDIO: "audio",
} as const;
export type Modality = (typeof Modality)[keyof typeof Modality];

export const MODALITIES: readonly Modality[] = [Modality.TEXT, Modality.IMAGE, Modality.AUDIO];

export function isModality(value: string): value is Modality {
  return (MODALITIES as readonly string[]).includes(value);
}

/**
 * Where data goes when it reaches a provider. The privacy boundary
 * (Contract 2) is defined over these three classes and nothing else.
 */
export const EgressClass = {
  DEVICE: "device", // never leaves the machine
  TRUSTED_NETWORK: "trusted-network", // self-hosted, user-controlled
  PUBLIC_CLOUD: "public-cloud", // third-party API
} as const;
export type EgressClass = (typeof EgressClass)[keyof typeof EgressClass];

export const EGRESS_CLASSES: readonly EgressClass[] = [
  EgressClass.DEVICE,
  EgressClass.TRUSTED_NETWORK,
  EgressClass.PUBLIC_CLOUD,
];

export function isEgressClass(value: string): value is EgressClass {
  return (EGRESS_CLASSES as readonly string[]).includes(value);
}

/**
 * Gemma 4 vision token budgets, exposed as named presets
 * (docs/brief.md §2, quirk 5).
 */
export const VISION_TOKEN_BUDGETS: Readonly<Record<string, number>> = {
  minimal: 70,
  caption: 140,
  default: 280,
  detail: 560,
  ocr: 1120,
};

export type ImageDetail = "minimal" | "caption" | "default" | "detail" | "ocr";

export interface CapabilityRequirements {
  modalitiesIn?: Iterable<Modality>;
  modalitiesOut?: Iterable<Modality>;
  toolCalling?: boolean;
  streaming?: boolean;
  contextTokens?: number;
  thinking?: boolean;
}

export interface CapabilitiesInit {
  modalitiesIn: Iterable<Modality>;
  modalitiesOut: Iterable<Modality>;
  toolCalling: boolean;
  streaming: boolean;
  contextTokens: number;
  thinking: boolean;
  egress: EgressClass;
}

/**
 * What a provider/model pair can do. Declared, never inferred.
 *
 * @example
 * const caps = new Capabilities({
 *   modalitiesIn: ["text", "audio"], modalitiesOut: ["text"],
 *   toolCalling: true, streaming: true, contextTokens: 131072,
 *   thinking: true, egress: "device",
 * });
 * caps.missing({ modalitiesIn: ["image"] }); // ["image input"]
 */
export class Capabilities {
  readonly modalitiesIn: ReadonlySet<Modality>;
  readonly modalitiesOut: ReadonlySet<Modality>;
  readonly toolCalling: boolean;
  readonly streaming: boolean;
  readonly contextTokens: number;
  readonly thinking: boolean;
  readonly egress: EgressClass;

  constructor(init: CapabilitiesInit) {
    this.modalitiesIn = new Set(init.modalitiesIn);
    this.modalitiesOut = new Set(init.modalitiesOut);
    this.toolCalling = init.toolCalling;
    this.streaming = init.streaming;
    this.contextTokens = init.contextTokens;
    this.thinking = init.thinking;
    this.egress = init.egress;
  }

  /**
   * Human-readable list of requirements this capability set lacks.
   *
   * An empty list means every requirement is met. These strings feed
   * `CapabilityMismatch` messages verbatim — they are part of the error
   * contract, not decoration, and so is their order: input modalities
   * (sorted by name), then output modalities (sorted), then tool calling,
   * streaming, context window, thinking.
   */
  missing(requirements: CapabilityRequirements = {}): string[] {
    const gaps: string[] = [];
    const wantedIn = [...new Set(requirements.modalitiesIn ?? [])]
      .filter((m) => !this.modalitiesIn.has(m))
      .sort();
    for (const m of wantedIn) gaps.push(`${m} input`);

    const wantedOut = [...new Set(requirements.modalitiesOut ?? [])]
      .filter((m) => !this.modalitiesOut.has(m))
      .sort();
    for (const m of wantedOut) gaps.push(`${m} output`);

    if (requirements.toolCalling && !this.toolCalling) gaps.push("tool calling");
    if (requirements.streaming && !this.streaming) gaps.push("streaming");
    const contextTokens = requirements.contextTokens ?? 0;
    if (contextTokens > this.contextTokens) {
      gaps.push(`context window (${contextTokens} needed, ${this.contextTokens} available)`);
    }
    if (requirements.thinking && !this.thinking) gaps.push("thinking");
    return gaps;
  }

  /** The `capabilities.schema.json` document for this record. */
  toJSON(): Record<string, unknown> {
    return {
      modalities_in: [...this.modalitiesIn].sort(),
      modalities_out: [...this.modalitiesOut].sort(),
      tool_calling: this.toolCalling,
      streaming: this.streaming,
      context_tokens: this.contextTokens,
      thinking: this.thinking,
      egress: this.egress,
    };
  }
}

// ── Messages ─────────────────────────────────────────────────────────────

export type Role = "system" | "user" | "assistant" | "tool";

/** Media payload: raw bytes, or a string that is a path or already base64. */
export type MediaData = Uint8Array | string;

export interface TextPart {
  readonly type: "text";
  readonly text: string;
}

export interface ImagePart {
  readonly type: "image";
  readonly data: MediaData;
  readonly detail: ImageDetail;
}

export interface AudioPart {
  readonly type: "audio";
  readonly data: MediaData;
  readonly format: string;
}

export type Part = TextPart | ImagePart | AudioPart;

export function textPart(text: string): TextPart {
  return { type: "text", text };
}

export function imagePart(data: MediaData, detail: ImageDetail = "default"): ImagePart {
  return { type: "image", data, detail };
}

export function audioPart(data: MediaData, format = "wav"): AudioPart {
  return { type: "audio", data, format };
}

/** A function call requested by the model (native function calling). */
export interface ToolCall {
  readonly id: string;
  readonly name: string;
  readonly arguments: Record<string, unknown>;
}

export interface MessageInit {
  role: Role;
  parts?: readonly Part[];
  /**
   * The model's thought block for assistant turns. Kept OUT of `parts` so
   * that stripping prior-turn thinking (quirk 3) is structural rather than
   * string surgery — the builder simply never serializes this field.
   */
  thinking?: string | null;
  toolCalls?: readonly ToolCall[];
  toolCallId?: string | null;
}

/** One conversation turn. */
export class Message {
  readonly role: Role;
  readonly parts: readonly Part[];
  readonly thinking: string | null;
  readonly toolCalls: readonly ToolCall[];
  readonly toolCallId: string | null;

  constructor(init: MessageInit) {
    this.role = init.role;
    this.parts = init.parts ?? [];
    this.thinking = init.thinking ?? null;
    this.toolCalls = init.toolCalls ?? [];
    this.toolCallId = init.toolCallId ?? null;
  }

  /**
   * Convenience constructor for a plain text turn.
   *
   * @example
   * Message.text("user", "hi").parts[0]; // { type: "text", text: "hi" }
   */
  static text(role: Role, text: string): Message {
    return new Message({ role, parts: [textPart(text)] });
  }

  /** The input modalities this message needs from a model. */
  requiredModalities(): Set<Modality> {
    const mods = new Set<Modality>();
    for (const part of this.parts) {
      if (part.type === "text") mods.add(Modality.TEXT);
      else if (part.type === "image") mods.add(Modality.IMAGE);
      else mods.add(Modality.AUDIO);
    }
    return mods;
  }
}

// ── Requests and responses ───────────────────────────────────────────────

export interface ToolDeclaration {
  readonly name: string;
  readonly description: string;
  asFunctionSchema(): Record<string, unknown>;
}

export interface GenerateRequestInit {
  messages: readonly Message[];
  model?: string | null;
  tools?: readonly ToolDeclaration[];
  think?: boolean;
  temperature?: number | null;
  topP?: number | null;
  topK?: number | null;
  maxTokens?: number | null;
  stop?: readonly string[];
}

/**
 * A single generation request.
 *
 * Sampling fields default to `null` meaning "apply the model's known-good
 * defaults" (Gemma 4: 1.0 / 0.95 / 64 — quirk 1). Set a value only to
 * override deliberately; `0` is an override, not an absent value.
 */
export class GenerateRequest {
  readonly messages: readonly Message[];
  readonly model: string | null;
  readonly tools: readonly ToolDeclaration[];
  readonly think: boolean;
  readonly temperature: number | null;
  readonly topP: number | null;
  readonly topK: number | null;
  readonly maxTokens: number | null;
  readonly stop: readonly string[];

  constructor(init: GenerateRequestInit) {
    this.messages = init.messages;
    this.model = init.model ?? null;
    this.tools = init.tools ?? [];
    this.think = init.think ?? false;
    this.temperature = init.temperature ?? null;
    this.topP = init.topP ?? null;
    this.topK = init.topK ?? null;
    this.maxTokens = init.maxTokens ?? null;
    this.stop = init.stop ?? [];
  }

  /** Union of input modalities across all messages. */
  requiredModalities(): Set<Modality> {
    const mods = new Set<Modality>();
    for (const message of this.messages) {
      for (const m of message.requiredModalities()) mods.add(m);
    }
    return mods;
  }
}

export interface Usage {
  readonly inputTokens: number;
  readonly outputTokens: number;
}

export function usage(inputTokens = 0, outputTokens = 0): Usage {
  return { inputTokens, outputTokens };
}

/**
 * A completed generation. `raw` always carries the unmodified provider
 * payload — the escape hatch that keeps this a toolkit, not a framework.
 */
export interface GenerateResponse {
  readonly text: string;
  readonly thinking?: string | null;
  readonly toolCalls?: readonly ToolCall[];
  readonly usage?: Usage;
  readonly model?: string;
  readonly raw?: Record<string, unknown>;
}

/**
 * One streaming increment. The final chunk has `done: true` and carries the
 * assembled `GenerateResponse`.
 */
export interface Chunk {
  readonly delta?: string;
  readonly thinkingDelta?: string;
  readonly toolCall?: ToolCall | null;
  readonly done?: boolean;
  readonly response?: GenerateResponse | null;
}

/**
 * The provider contract. Anything satisfying it plugs in identically,
 * including third-party plugins.
 */
export interface Provider {
  readonly name: string;
  capabilities(): Capabilities;
  generate(req: GenerateRequest): GenerateResponse | Promise<GenerateResponse>;
  stream?(req: GenerateRequest): AsyncIterable<Chunk> | Iterable<Chunk>;
}
