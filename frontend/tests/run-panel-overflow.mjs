/* Unlike the other suites, this one reads the panel sources as text instead of
 * importing them, so it must not be bundled into /tmp — `import.meta.url` has
 * to keep resolving against this directory. Node 22 strips the TS types. */
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const test = fileURLToPath(new URL("./panel-overflow.test.ts", import.meta.url));
const result = spawnSync(
  process.execPath,
  ["--experimental-strip-types", "--no-warnings", test],
  { stdio: "inherit" },
);
process.exit(result.status ?? 1);
