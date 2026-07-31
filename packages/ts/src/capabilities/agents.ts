/**
 * Tool-calling agent loop with a step budget and cooperative cancellation.
 *
 * The loop sends the running message history to the provider, dispatches any
 * requested tool calls, and repeats until the model answers in plain text,
 * the step budget is exhausted, or the caller cancels. It never throws on
 * budget exhaustion — that is a normal, reportable stop condition
 * (`stoppedReason`), not an error.
 *
 * Offline mode (`offline: true`) dispatches every tool call to `spec.mock`
 * instead of `spec.fn`. Per Contract 3, that swap happens here — in the
 * agent loop — never inside `Tool` itself.
 *
 * @example
 * const loop = new AgentLoop(scriptedProvider, [add], { offline: true });
 * const result = await loop.run("what is 3 + 4?");
 * result.stoppedReason; // "done"
 */

import {
  EgressClass,
  GenerateRequest,
  GenerateResponse,
  Message,
  Provider,
  textPart,
  ToolCall,
} from "../contracts/provider.js";
import { Tool } from "../contracts/tools.js";
import { TraceEvent } from "../contracts/trace.js";

export type StoppedReason = "done" | "budget" | "cancelled";

/** One step of the loop: the model response and any tool dispatches. */
export interface StepRecord {
  readonly index: number;
  readonly response: GenerateResponse;
  /** Each dispatched call paired with the text fed back to the model. */
  readonly toolResults: readonly (readonly [ToolCall, string])[];
}

/** Outcome of a full `AgentLoop.run()`. */
export interface AgentResult {
  readonly finalText: string;
  readonly steps: readonly StepRecord[];
  readonly stoppedReason: StoppedReason;
}

export interface AgentLoopOptions {
  maxSteps?: number;
  offline?: boolean;
  emit?: ((event: TraceEvent) => void) | null;
}

export interface RunOptions {
  /** Cooperative cancellation; checked before each model call. */
  signal?: AbortSignal;
}

/** Best-effort text form of a tool's return value. */
function stringify(result: unknown): string {
  if (typeof result === "string") return result;
  if (result === undefined) return "undefined";
  try {
    return JSON.stringify(result) ?? String(result);
  } catch {
    return String(result);
  }
}

let spanCounter = 0;
function nextId(prefix: string): string {
  spanCounter += 1;
  return `${prefix}-${spanCounter}`;
}

/** Drives a provider through a tool-calling loop. */
export class AgentLoop {
  readonly provider: Provider;
  readonly tools: Map<string, Tool>;
  readonly maxSteps: number;
  readonly offline: boolean;
  readonly emit: ((event: TraceEvent) => void) | null;

  constructor(provider: Provider, tools: readonly Tool[], options: AgentLoopOptions = {}) {
    this.provider = provider;
    this.tools = new Map(tools.map((t) => [t.spec.name, t]));
    this.maxSteps = options.maxSteps ?? 8;
    this.offline = options.offline ?? false;
    this.emit = options.emit ?? null;
  }

  /**
   * Run the loop to completion, to the step budget, or to cancellation.
   * Never throws on a normal stop condition — `stoppedReason` tells the
   * caller what happened.
   */
  async run(input: string | readonly Message[], options: RunOptions = {}): Promise<AgentResult> {
    const history: Message[] =
      typeof input === "string" ? [Message.text("user", input)] : [...input];

    const runId = nextId("agent-run");
    const steps: StepRecord[] = [];
    let lastText = "";

    for (let stepIndex = 0; stepIndex < this.maxSteps; stepIndex += 1) {
      if (options.signal?.aborted) {
        return { finalText: lastText, steps, stoppedReason: "cancelled" };
      }

      const req = new GenerateRequest({
        messages: history,
        tools: [...this.tools.values()].map((t) => t.spec),
      });
      const response = await this.provider.generate(req);
      lastText = response.text;
      const toolCalls = response.toolCalls ?? [];

      if (toolCalls.length === 0) {
        steps.push({ index: stepIndex, response, toolResults: [] });
        return { finalText: response.text, steps, stoppedReason: "done" };
      }

      history.push(
        new Message({
          role: "assistant",
          parts: response.text ? [textPart(response.text)] : [],
          toolCalls,
        }),
      );

      const toolResults: (readonly [ToolCall, string])[] = [];
      for (const call of toolCalls) {
        const resultText = await this.#dispatch(call, runId);
        toolResults.push([call, resultText]);
        history.push(
          new Message({
            role: "tool",
            parts: [textPart(resultText)],
            toolCallId: call.id,
          }),
        );
      }

      steps.push({ index: stepIndex, response, toolResults });
    }

    return { finalText: lastText, steps, stoppedReason: "budget" };
  }

  /**
   * Dispatch one requested tool call. Unknown tool names never crash the
   * loop — they feed an error result back to the model.
   */
  async #dispatch(call: ToolCall, runId: string): Promise<string> {
    const tool = this.tools.get(call.name);
    if (tool === undefined) {
      this.#emitEvent(runId, { tool: call.name, permissions: [], mock: this.offline });
      return `error: unknown tool '${call.name}'`;
    }

    const fn = this.offline ? tool.spec.mock : tool.spec.fn;
    this.#emitEvent(runId, {
      tool: tool.spec.name,
      permissions: [...tool.spec.permissions].sort(),
      mock: this.offline,
    });
    try {
      return stringify(await fn(call.arguments));
    } catch (exc) {
      return `error: ${(exc as Error).message ?? String(exc)}`;
    }
  }

  #emitEvent(runId: string, payload: Record<string, unknown>): void {
    if (this.emit === null) return;
    this.emit(
      new TraceEvent({
        ts: Date.now() / 1000,
        runId,
        spanId: nextId("agent-span"),
        parentSpanId: null,
        kind: "tool",
        egress: EgressClass.DEVICE,
        bytesOut: 0,
        payload,
      }),
    );
  }
}
