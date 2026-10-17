import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";

export const HASH_REGEX = /<!--\s*MYCRO_BUILD_HASH:([a-f0-9]{32,64})\s*-->/;

function walkDir(dir, fileList = []) {
	if (!fs.existsSync(dir)) return fileList;
	const entries = fs.readdirSync(dir, { withFileTypes: true });
	for (const entry of entries) {
		const fullPath = path.join(dir, entry.name);
		if (entry.isDirectory()) {
			if (entry.name !== "node_modules" && entry.name !== "dist" && entry.name !== ".git") {
				walkDir(fullPath, fileList);
			}
		} else if (entry.isFile()) {
			fileList.push(fullPath);
		}
	}
	return fileList;
}

export function computeSourceHash(webDir) {
	const hash = crypto.createHash("sha256");

	const explicitFiles = [
		path.join(webDir, "index.html"),
		path.join(webDir, "vite.config.ts"),
		path.join(webDir, "package.json"),
		path.join(webDir, "tsconfig.json")
	].filter(p => fs.existsSync(p));

	const srcFiles = walkDir(path.join(webDir, "src"));
	const allFiles = [...explicitFiles, ...srcFiles];

	// Sort deterministically by relative path
	allFiles.sort((a, b) => path.relative(webDir, a).localeCompare(path.relative(webDir, b)));

	for (const filePath of allFiles) {
		const rel = path.relative(webDir, filePath);
		hash.update(rel);
		hash.update(fs.readFileSync(filePath));
	}

	return hash.digest("hex");
}

export function getRecordedHash(targetHtmlPath) {
	if (!fs.existsSync(targetHtmlPath)) return null;
	const content = fs.readFileSync(targetHtmlPath, "utf-8");
	const match = content.match(HASH_REGEX);
	return match ? match[1] : null;
}
