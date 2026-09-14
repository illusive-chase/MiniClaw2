import { build } from "esbuild";
import { pathToFileURL } from "node:url";

const outfile = "/tmp/miniclaw2-context-bundles.test.mjs";
await build({
  entryPoints: [new URL("./context-bundle-sources.test.ts", import.meta.url).pathname],
  outfile, bundle: true, platform: "node", format: "esm", logLevel: "warning",
  define: { "import.meta.url": JSON.stringify(new URL("./context-bundle-sources.test.ts", import.meta.url).href) },
});
await import(`${pathToFileURL(outfile).href}?run=${Date.now()}`);
