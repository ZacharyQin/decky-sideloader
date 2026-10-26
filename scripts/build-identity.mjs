import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const digest = value => createHash("sha256").update(value).digest("hex");
function sources(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? sources(path) : /\.(ts|tsx|css)$/.test(entry.name) ? [path] : [];
  });
}

export default function buildIdentity() {
  let identity;
  return {
    name: "sideload-build-identity",
    buildStart() {
      const files = [...sources(join(root, "src")), ...["package.json", "pnpm-lock.yaml", "rollup.config.js", "scripts/build-identity.mjs", "scripts/license-notices.mjs"].map(name => join(root, name))].sort();
      const hash = createHash("sha256");
      for (const file of files) {
        this.addWatchFile(file);
        hash.update(relative(root, file)).update("\0").update(readFileSync(file)).update("\0");
      }
      identity = { version: JSON.parse(readFileSync(join(root, "package.json"), "utf8")).version, fingerprint: hash.digest("hex") };
    },
    transform(_code, id) {
      if (resolve(id) === join(root, "src/build.ts")) return { code: `export const frontendIdentity = ${JSON.stringify(identity)};`, map: { mappings: "" } };
      return null;
    },
    generateBundle(_options, bundle) {
      const chunk = Object.values(bundle).find(item => item.type === "chunk" && item.isEntry);
      if (!chunk) throw new Error("Frontend entry bundle missing");
      this.emitFile({ type: "asset", fileName: "build.json", source: JSON.stringify({ ...identity, bundle: digest(chunk.code) }) + "\n" });
    },
  };
}
