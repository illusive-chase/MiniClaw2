import { build } from "esbuild";
import { pathToFileURL } from "node:url";

const outfile = "/tmp/miniclaw2-canvas-layout.test.mjs";
await build({
  entryPoints: [new URL("./canvas-layout.test.ts", import.meta.url).pathname],
  outfile,
  bundle: true,
  platform: "node",
  format: "esm",
  logLevel: "warning",
});
await import(`${pathToFileURL(outfile).href}?run=${Date.now()}`);
await build({
  entryPoints: [new URL("./node-positions.test.ts", import.meta.url).pathname],
  outfile: "/tmp/miniclaw2-node-positions.test.mjs",
  bundle: true,
  platform: "node",
  format: "esm",
  logLevel: "warning",
});
await import(`${pathToFileURL("/tmp/miniclaw2-node-positions.test.mjs").href}?run=${Date.now()}`);
await build({
  entryPoints: [new URL("./artifact-positions.test.ts", import.meta.url).pathname],
  outfile: "/tmp/miniclaw2-artifact-positions.test.mjs",
  bundle: true,
  platform: "node",
  format: "esm",
  logLevel: "warning",
});
await import(`${pathToFileURL("/tmp/miniclaw2-artifact-positions.test.mjs").href}?run=${Date.now()}`);
