/**
 * agenticcarekit — TypeScript port (Tier 1).
 *
 * Built against, and only against, the five frozen contracts in
 * `docs/CONTRACTS.md`. The kernel is verified by the shared conformance
 * corpus (`spec/conformance/`, run via
 * `spec/conformance/adapters/typescript.mjs`); the capability ports are
 * unit-tested only. `README.md` carries the support matrix — nothing here
 * claims coverage it does not have.
 */

export * from "./contracts/index.js";
export * from "./kernel/index.js";
export * from "./capabilities/index.js";
