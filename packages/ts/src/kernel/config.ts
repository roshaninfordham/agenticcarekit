/**
 * Contract 5 — `ack.toml`: declarative project state.
 *
 * The generator writes it, the runtime reads it, agents edit it, and
 * `ack sync` reconciles the tree against it. Unknown keys are **preserved**
 * (`raw` holds the full parsed document) — users and agents may extend the
 * file, and `ack sync` must never destroy their edits.
 *
 * Example `ack.toml`:
 *
 * ```toml
 * [project]
 * blueprint = "voice-care"
 * pack = "healthcare"
 *
 * [model]
 * primary = "ollama:gemma4:e4b-mlx"
 * fallback = "cerebras:gemma-4-31b"
 *
 * [policy]
 * egress = "device"
 * redactor = "healthcare.phi"
 *
 * [capabilities]
 * enabled = ["voice", "extract"]
 * ```
 */

import { readFileSync } from "node:fs";

import { AckError } from "../contracts/errors.js";
import { EgressClass, isEgressClass } from "../contracts/provider.js";
import { parseToml, TomlParseError } from "./toml.js";

/**
 * A `provider:model` reference, e.g. `ollama:gemma4:e4b-mlx`.
 *
 * @example
 * ModelRef.parse("ollama:gemma4:e4b-mlx"); // { provider: "ollama", model: "gemma4:e4b-mlx" }
 */
export class ModelRef {
  readonly provider: string;
  readonly model: string;

  constructor(provider: string, model: string) {
    this.provider = provider;
    this.model = model;
  }

  /** Splits on the FIRST colon only; no separator means no reference. */
  static parse(ref: string): ModelRef {
    const index = typeof ref === "string" ? ref.indexOf(":") : -1;
    const provider = index === -1 ? "" : ref.slice(0, index);
    const model = index === -1 ? "" : ref.slice(index + 1);
    if (index === -1 || provider.length === 0 || model.length === 0) {
      throw new AckError(`invalid model reference '${ref}'`, {
        code: "E401",
        why: "model references have the form provider:model, e.g. ollama:gemma4:e4b",
        fix: 'set [model] primary = "ollama:gemma4:e4b" in ack.toml',
      });
    }
    return new ModelRef(provider, model);
  }

  toString(): string {
    return `${this.provider}:${this.model}`;
  }
}

export interface AckConfigInit {
  blueprint: string;
  pack: string;
  modelPrimary: ModelRef;
  modelFallback?: ModelRef | null;
  egress?: EgressClass;
  redactor?: string | null;
  capabilities?: readonly string[];
  raw?: Record<string, unknown>;
}

/**
 * Parsed `ack.toml`. Every field is overridable by the user; the runtime
 * never rewrites this file except through `ack sync`/`ack add`, which
 * preserve user edits.
 */
export class AckConfig {
  readonly blueprint: string;
  readonly pack: string;
  readonly modelPrimary: ModelRef;
  readonly modelFallback: ModelRef | null;
  readonly egress: EgressClass;
  readonly redactor: string | null;
  readonly capabilities: readonly string[];
  readonly raw: Record<string, unknown>;

  constructor(init: AckConfigInit) {
    this.blueprint = init.blueprint;
    this.pack = init.pack;
    this.modelPrimary = init.modelPrimary;
    this.modelFallback = init.modelFallback ?? null;
    this.egress = init.egress ?? EgressClass.DEVICE;
    this.redactor = init.redactor ?? null;
    this.capabilities = init.capabilities ?? [];
    this.raw = init.raw ?? {};
  }

  /**
   * Build from a parsed TOML document, with E4xx errors naming the
   * missing/invalid key exactly.
   *
   * @throws AckError E402 — a required section is missing.
   * @throws AckError E403 — `[policy] egress` is not one of the three classes.
   * @throws AckError E401 — a model reference is unparseable.
   */
  static fromDict(d: Record<string, unknown>): AckConfig {
    for (const section of ["project", "model"]) {
      if (!(section in d)) {
        throw new AckError(`ack.toml is missing the [${section}] section`, {
          code: "E402",
          why: "[project] and [model] are required sections.",
          fix: "run `ack sync` to regenerate a valid ack.toml, or see docs/CONTRACTS.md",
        });
      }
    }
    const project = (d["project"] ?? {}) as Record<string, unknown>;
    const model = (d["model"] ?? {}) as Record<string, unknown>;
    const policy = (d["policy"] ?? {}) as Record<string, unknown>;
    const caps = (d["capabilities"] ?? {}) as Record<string, unknown>;

    const rawEgress = (policy["egress"] ?? EgressClass.DEVICE) as string;
    if (!isEgressClass(rawEgress)) {
      throw new AckError(`invalid [policy] egress '${rawEgress}'`, {
        code: "E403",
        why: "egress must be one of: device, trusted-network, public-cloud",
        fix: 'set [policy] egress = "device"',
      });
    }

    const fallback = model["fallback"];
    return new AckConfig({
      blueprint: (project["blueprint"] as string) ?? "",
      pack: (project["pack"] as string) ?? "",
      modelPrimary: ModelRef.parse(model["primary"] as string),
      modelFallback: fallback ? ModelRef.parse(fallback as string) : null,
      egress: rawEgress,
      redactor: (policy["redactor"] as string | undefined) ?? null,
      capabilities: [...((caps["enabled"] as string[] | undefined) ?? [])],
      raw: d,
    });
  }

  /**
   * Parse `ack.toml` text.
   *
   * @throws AckError E401 — the file is not valid TOML. Hand-edited files
   *   are an expected condition with a named fix, not a stack trace.
   */
  static parse(text: string): AckConfig {
    let data: Record<string, unknown>;
    try {
      data = parseToml(text) as Record<string, unknown>;
    } catch (exc) {
      if (exc instanceof TomlParseError) {
        throw new AckError(`ack.toml is not valid TOML: ${exc.message}`, {
          code: "E401",
          why: "the file was probably hand-edited into an invalid state.",
          fix: "fix the TOML syntax, or regenerate with `ack sync`",
        });
      }
      throw exc;
    }
    return AckConfig.fromDict(data);
  }

  /** Read and parse an `ack.toml` file. */
  static load(path: string): AckConfig {
    let text: string;
    try {
      text = readFileSync(path, "utf-8");
    } catch {
      throw new AckError(`no ack.toml found at ${path}`, {
        code: "E404",
        why: "this directory is not an agenticcarekit project (or you are in the wrong directory).",
        fix: "cd into your project, or create one: ack init",
      });
    }
    return AckConfig.parse(text);
  }

  /**
   * Serialize deterministically — byte-identical for identical configs
   * (invariant 4), and a fixpoint under parse → serialize → parse.
   */
  toToml(): string {
    const lines = [
      "[project]",
      `blueprint = "${this.blueprint}"`,
      `pack = "${this.pack}"`,
      "",
      "[model]",
      `primary = "${this.modelPrimary}"`,
    ];
    if (this.modelFallback) lines.push(`fallback = "${this.modelFallback}"`);
    lines.push("", "[policy]", `egress = "${this.egress}"`);
    if (this.redactor) lines.push(`redactor = "${this.redactor}"`);
    lines.push(
      "",
      "[capabilities]",
      `enabled = [${this.capabilities.map((c) => `"${c}"`).join(", ")}]`,
      "",
    );
    return lines.join("\n");
  }
}
