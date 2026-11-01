// Installs the realm package without its lifecycle scripts (they send analytics), then fetches only the native prebuild.
import { execSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
execSync("npm install --ignore-scripts --no-audit --no-fund", { cwd: here, stdio: "inherit" });
const bin = path.join(here, "node_modules", ".bin", "prebuild-install");
execSync(`"${bin}" --runtime napi`, { cwd: path.join(here, "node_modules", "realm"), stdio: "inherit" });
