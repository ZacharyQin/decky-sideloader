import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const require = createRequire(import.meta.url);
const read = path => readFileSync(path, "utf8");
const metadata = path => JSON.parse(read(join(path, "package.json")));
const document = (title, text) => {
  if (!text.trim()) throw new Error(`Empty license document: ${title}`);
  return { title, text };
};
const retained = file => document(file, read(join(root, "licenses", file)));
const groups = new Map([
  ["@decky/api", "decky"], ["react-icons", "icons"], ["qrcode-generator", "qr"],
  ["compare-versions", "versions"], ...["react-markdown", "remark-gfm", "rehype-raw", "rehype-sanitize"].map(name => [name, "markdown"]),
]);
function packageRoot(file) {
  let directory = dirname(file);
  while (directory !== dirname(directory)) {
    if (existsSync(join(directory, "package.json")) && metadata(directory).name) return directory;
    directory = dirname(directory);
  }
  throw new Error(`Package not found for ${file}`);
}
function packageEntry(directory, group, host = false) {
  const pkg = metadata(directory);
  const files = readdirSync(directory).filter(name => /^(licen[cs]e|copying|notice)(\.|$)/i.test(name)).sort();
  const documents = pkg.name === "qrcode-generator" ? [retained("qrcode-generator-MIT.txt")] : files.map(name => document(name, read(join(directory, name))));
  if (!documents.length || typeof pkg.license !== "string") throw new Error(`Missing license: ${pkg.name}`);
  const repository = typeof pkg.repository === "string" ? pkg.repository : pkg.repository?.url;
  const repositoryUrl = (repository || pkg.homepage || `https://www.npmjs.com/package/${pkg.name}`)
    .replace(/^git\+/, "").replace(/^git:\/\//, "https://").replace(/^ssh:\/\/git@([^/:]+)[:/]/, "https://$1/").replace(/\.git$/, "");
  const source = /^(?:github:)?[\w.-]+\/[\w.-]+(?:#.*)?$/.test(repositoryUrl)
    ? `https://github.com/${repositoryUrl.replace(/^github:/, "")}` : repositoryUrl;
  const author = (typeof pkg.author === "string" ? pkg.author : pkg.author?.name) || "";
  const copyright = [...new Set(documents.flatMap(doc => doc.text.split("\n")
    .filter(line => /^\s*Copyright\s+(?:\(c\)\s*)?(?:\d{4}|©)/i.test(line)).map(line => line.trim())))].join("\n");
  const attribution = pkg.license.startsWith("LGPL") ? author || copyright : copyright || author;
  return { id: pkg.name, name: pkg.name, version: pkg.version, group, host, license: pkg.license, source, attribution, documents };
}

export default function licenseNotices() {
  return {
    name: "sideload-license-notices",
    generateBundle(_options, bundle) {
      const chunks = Object.values(bundle).filter(item => item.type === "chunk");
      const modules = chunks.flatMap(chunk => Object.entries(chunk.modules).filter(([, info]) => info.renderedLength > 0).map(([id]) => id));
      const roots = new Map();
      for (const id of modules) {
        if (!id.includes("/node_modules/") || id.startsWith("\0")) continue;
        const directory = packageRoot(id);
        roots.set(metadata(directory).name, directory);
      }
      const own = metadata(root);
      const entries = [{ id: "project", name: "Sideloader", version: own.version, group: "project", host: false,
        license: "BSD-3-Clause", source: "https://github.com/SteamDeckHomebrew/decky-plugin-template",
        attribution: "Copyright (c) 2026, zacharym\nOriginal Copyright (c) 2022-2024, Steam Deck Homebrew",
        documents: [document("LICENSE", read(join(root, "LICENSE")))] }];
      for (const [name, directory] of [...roots].sort(([a], [b]) => a.localeCompare(b))) {
        const entry = packageEntry(directory, groups.get(name) || "transitive");
        if (name === "micromark-util-html-tag-name") {
          const source = read(join(directory, "index.js"));
          const attribution = source.match(/^\/\*\*([\s\S]*?)\*\//)?.[1].replace(/^ \* ?/gm, "").trim();
          if (!attribution) throw new Error("Recheck CommonMark tag table attribution");
          entry.documents.push(document("CommonMark", attribution));
        }
        entries.push(entry);
      }
      entries.push(packageEntry(resolve(root, "node_modules/@decky/ui"), "decky", true));
      if (modules.some(id => id.includes("/react-icons/fa/"))) entries.push({
        id: "font-awesome-5", name: "Font Awesome Free 5", version: "5.15.4-3-gafecf2a", group: "icons", host: false,
        license: "CC-BY-4.0", source: "https://github.com/FortAwesome/Font-Awesome/tree/afecf2a", attribution: "Fonticons, Inc.",
        note: "font-awesome-svg", documents: [retained("font-awesome-5.txt"), retained("CC-BY-4.0.txt")],
      });
      const pathModule = modules.find(id => id.endsWith("/vfile/lib/minpath.browser.js"));
      if (pathModule) {
        const header = read(pathModule).match(/^(?:\/\/[^\n]*\n)+/)?.[0].replace(/^\/\/ ?/gm, "");
        const parts = header?.split("A derivative work based on:").filter(Boolean);
        if (parts?.length !== 2) throw new Error("Recheck vfile embedded path notices");
        for (const [index, name, source] of [[0, "path-browserify", "https://github.com/browserify/path-browserify"], [1, "Node.js path", "https://github.com/nodejs/node/blob/main/lib/path.js"]]) {
          entries.push({ id: name, name, version: `vfile ${metadata(roots.get("vfile")).version}`, group: "embedded", host: false,
            license: "MIT", source, attribution: parts[index].split("\n").find(line => line.includes("Copyright")),
            documents: [document("MIT", parts[index].trim())] });
        }
      }
      if (modules.some(id => id.endsWith("/entities/dist/esm/decode-codepoint.js"))) entries.push({
        id: "he", name: "he", version: "36afe179392226cf1b6ccdb16ebbb7a5a844d93a", group: "embedded", host: false,
        license: "MIT", source: "https://github.com/mathiasbynens/he/tree/36afe179392226cf1b6ccdb16ebbb7a5a844d93a", attribution: "Mathias Bynens",
        documents: [retained("he-MIT.txt")],
      });
      if (modules.some(id => id.includes("commonjsHelpers"))) {
        const rollupRequire = createRequire(require.resolve("@decky/rollup"));
        const helper = packageEntry(packageRoot(rollupRequire.resolve("@rollup/plugin-commonjs")), "embedded");
        entries.push({ ...helper, name: "CommonJS helper" });
      }
      this.emitFile({ type: "asset", fileName: "notices.json", source: JSON.stringify({ entries }, null, 2) + "\n" });
    },
  };
}
