#!/usr/bin/env node
/**
 * Copy non-TypeScript assets into dist/.
 *
 * Prompts are `.md` files, never string literals (docs/CONTRACTS.md,
 * conventions), and `tsc` only emits `.ts`. This is the whole build step
 * beyond the compiler — no bundler, no plugin, no dependency.
 */

import { cp, mkdir } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));

const ASSETS = [["src/capabilities/prompts", "dist/src/capabilities/prompts"]];

for (const [from, to] of ASSETS) {
  await mkdir(join(ROOT, dirname(to)), { recursive: true });
  await cp(join(ROOT, from), join(ROOT, to), { recursive: true });
}
