import { build } from "esbuild";
import { pathToFileURL } from "node:url";

const outfile = "/tmp/miniclaw2-sync-status.test.mjs";
await build({
  entryPoints: [new URL("./sync-status.test.ts", import.meta.url).pathname],
  outfile,
  bundle: true,
  platform: "node",
  format: "esm",
  logLevel: "warning",
});
await import(`${pathToFileURL(outfile).href}?run=${Date.now()}`);
