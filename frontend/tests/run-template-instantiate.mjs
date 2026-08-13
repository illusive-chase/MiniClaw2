import { build } from "esbuild";
import { pathToFileURL } from "node:url";

const outfile = "/tmp/miniclaw2-template-instantiate.test.mjs";
await build({
  entryPoints: [new URL("./template-instantiate.test.ts", import.meta.url).pathname],
  outfile,
  bundle: true,
  platform: "node",
  format: "esm",
  logLevel: "warning",
});
await import(`${pathToFileURL(outfile).href}?run=${Date.now()}`);
