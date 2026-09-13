import { build } from "esbuild";
import { pathToFileURL } from "node:url";

const outfile = "/tmp/miniclaw2-permission-dialog.test.cjs";
await build({
  entryPoints: [new URL("./permission-dialog.test.ts", import.meta.url).pathname],
  outfile,
  bundle: true,
  platform: "node",
  format: "cjs",
  logLevel: "warning",
});
await import(`${pathToFileURL(outfile).href}?run=${Date.now()}`);
