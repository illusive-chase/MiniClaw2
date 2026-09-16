import { build } from "esbuild";
import { pathToFileURL } from "node:url";

const outfile = "/tmp/miniclaw2-storage-maintenance.test.mjs";
await build({
  entryPoints: [new URL("./storage-maintenance.test.ts", import.meta.url).pathname],
  outfile,
  bundle: true,
  platform: "node",
  format: "esm",
  logLevel: "warning",
  define: { "import.meta.url": JSON.stringify(new URL("./storage-maintenance.test.ts", import.meta.url).href) },
});
await import(`${pathToFileURL(outfile).href}?run=${Date.now()}`);
