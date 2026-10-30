import deckyPlugin from "@decky/rollup";
import terser from "@rollup/plugin-terser";
import licenseNotices from "./scripts/license-notices.mjs";
import buildIdentity from "./scripts/build-identity.mjs";

const config = deckyPlugin({
  plugins: [buildIdentity(), licenseNotices(), terser({ maxWorkers: 1 })],
});

export default {
  ...config,
  output: { ...config.output, sourcemap: false },
};
