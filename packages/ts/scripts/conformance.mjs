#!/usr/bin/env node
/**
 * Run the shared conformance corpus against this package's adapter.
 *
 * The corpus is shared, not copied (spec/README.md): a port that vendors its
 * own fixtures has forked the spec. So this is a thin wrapper around the
 * canonical harness — extra arguments are forwarded, e.g.
 *
 *     npm run conformance -- --filter policy -v
 */

import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const PACKAGE_ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const REPO_ROOT = join(PACKAGE_ROOT, "..", "..");
const RUNNER = join(REPO_ROOT, "spec", "conformance", "runner.py");
const ADAPTER = join(REPO_ROOT, "spec", "conformance", "adapters", "typescript.mjs");

const result = spawnSync(
  "python3",
  [RUNNER, ...process.argv.slice(2), "node", ADAPTER],
  { stdio: "inherit", cwd: REPO_ROOT },
);

if (result.error) {
  console.error(
    `cannot run the conformance harness: ${result.error.message}\n` +
      "The harness is stdlib-only Python; install python3 or run the corpus " +
      "from your own runner (spec/conformance/README.md).",
  );
  process.exit(2);
}
process.exit(result.status ?? 2);
