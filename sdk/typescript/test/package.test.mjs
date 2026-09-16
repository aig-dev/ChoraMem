import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import { it } from "vitest";

const sdkRoot = fileURLToPath(new URL("..", import.meta.url));

it("ships a generic lifecycle example that compiles through the public entry", () => {
  execFileSync("./test/check-package.sh", {
    cwd: sdkRoot,
    stdio: "pipe",
  });
});
