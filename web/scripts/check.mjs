#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { computeSourceHash, getRecordedHash } from "./hash.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.resolve(__dirname, "..");
const repoRoot = path.resolve(webDir, "..");
const targetHtml = path.resolve(repoRoot, "src", "index.html");
const targetGz = path.resolve(repoRoot, "src", "index.html.gz");

const currentHash = computeSourceHash(webDir);
const recordedHash = getRecordedHash(targetHtml);

if (fs.existsSync(targetGz) && recordedHash && recordedHash === currentHash) {
	console.log(`up-to-date (hash: ${currentHash.slice(0, 12)})`);
	process.exit(0);
} else {
	console.log(`outdated (current: ${currentHash.slice(0, 12)}, recorded: ${recordedHash ? recordedHash.slice(0, 12) : "none"})`);
	process.exit(1);
}
