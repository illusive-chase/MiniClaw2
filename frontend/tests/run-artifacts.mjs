import { build } from "esbuild";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const temporary = await mkdtemp(join(tmpdir(), "miniclaw2-artifacts-"));
const outfile = join(temporary, "artifacts.test.mjs");
try {
  await build({
    entryPoints: [new URL("./artifacts.test.tsx", import.meta.url).pathname],
    outfile,
    bundle: true,
    platform: "node",
    format: "esm",
    logLevel: "warning",
  });
  await import(pathToFileURL(outfile).href);
} finally {
  await rm(temporary, { recursive: true, force: true });
}
