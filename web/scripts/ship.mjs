// Copy the static export into the Python package, where loft/desktop.py serves it (and where PyInstaller/hatch pick it up).
import { cpSync, existsSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const from = join(root, "out"), to = join(root, "..", "loft", "web_ui");
if (!existsSync(from)) throw new Error("no out/ directory: did `next build` run?");
rmSync(to, { recursive: true, force: true });
cpSync(from, to, { recursive: true });
console.log("UI copied to loft/web_ui");
