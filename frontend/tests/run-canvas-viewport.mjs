import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { build } from "esbuild";

const temporary = await mkdtemp(join(tmpdir(), "miniclaw2-viewport-"));
try {
  const outfile = join(temporary, "test.mjs");
  await build({
    entryPoints: [new URL("./canvas-viewport.test.ts", import.meta.url).pathname],
    outfile, bundle: true, platform: "node", format: "esm", logLevel: "warning",
  });
  await import(pathToFileURL(outfile).href);
} finally {
  await rm(temporary, { recursive: true, force: true });
}
