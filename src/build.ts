declare const __SIDELOAD_FRONTEND__: { version: string; fingerprint: string };
// The Rollup build replaces this module with its independently calculated identity.
export const frontendIdentity = typeof __SIDELOAD_FRONTEND__ === "undefined" ? null : __SIDELOAD_FRONTEND__;
