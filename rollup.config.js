import deckyPlugin from "@decky/rollup";
import licenseNotices from "./scripts/license-notices.mjs";
import buildIdentity from "./scripts/build-identity.mjs";

export default deckyPlugin({
  plugins: [buildIdentity(), licenseNotices()],
});
