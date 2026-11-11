#!/usr/bin/env node
import fs from "node:fs";
import { spawn } from "node:child_process";

const CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9222;

async function sleep(ms) {
	return new Promise(r => setTimeout(r, ms));
}

async function main() {
	const chrome = spawn(CHROME_PATH, [
		"--headless=new",
		`--remote-debugging-port=${PORT}`,
		"--window-size=1280,900",
		"--disable-gpu",
		"--no-sandbox",
		"about:blank"
	]);

	try {
		let targets = null;
		for (let i = 0; i < 20; i++) {
			await sleep(200);
			try {
				const res = await fetch(`http://127.0.0.1:${PORT}/json`);
				if (res.ok) {
					targets = await res.json();
					break;
				}
			} catch {}
		}

		const pageTarget = targets.find(t => t.type === "page") || targets[0];
		const ws = new WebSocket(pageTarget.webSocketDebuggerUrl);
		await new Promise(r => ws.onopen = r);

		let id = 1;
		const pending = new Map();
		ws.onmessage = e => {
			const d = JSON.parse(e.data);
			if (d.id && pending.has(d.id)) {
				pending.get(d.id)(d.result);
				pending.delete(d.id);
			}
		};
		const send = (method, params = {}) => new Promise(r => {
			const curId = id++;
			pending.set(curId, r);
			ws.send(JSON.stringify({ id: curId, method, params }));
		});

		await send("Page.enable");
		await send("Runtime.enable");
		await send("Page.navigate", { url: "http://localhost:5173/" });
		await sleep(600);

		// Open drawer
		await send("Runtime.evaluate", {
			expression: "document.getElementById('openManualBtn').click()",
			awaitPromise: true
		});
		await sleep(400);

		// Screenshot 1: Collapse All
		await send("Runtime.evaluate", {
			expression: "document.getElementById('manualCollapseAllBtn').click()",
			awaitPromise: true
		});
		await sleep(200);
		let shot = await send("Page.captureScreenshot", { format: "png" });
		fs.writeFileSync("/tmp/drawer_collapsed.png", Buffer.from(shot.data, "base64"));
		console.log("Saved /tmp/drawer_collapsed.png");

		// Screenshot 2: Expand All and Search "click"
		await send("Runtime.evaluate", {
			expression: `
				const input = document.getElementById('manualSearchInput');
				input.value = 'click';
				input.dispatchEvent(new Event('input', { bubbles: true }));
			`,
			awaitPromise: true
		});
		await sleep(200);
		shot = await send("Page.captureScreenshot", { format: "png" });
		fs.writeFileSync("/tmp/drawer_search_click.png", Buffer.from(shot.data, "base64"));
		console.log("Saved /tmp/drawer_search_click.png");

		// Screenshot 3: Clear search and scroll down to Recipes
		await send("Runtime.evaluate", {
			expression: `
				document.getElementById('manualSearchClear').click();
				const body = document.getElementById('manualDrawerContent');
				body.scrollTop = body.scrollHeight;
			`,
			awaitPromise: true
		});
		await sleep(200);
		shot = await send("Page.captureScreenshot", { format: "png" });
		fs.writeFileSync("/tmp/drawer_recipes_scroll.png", Buffer.from(shot.data, "base64"));
		console.log("Saved /tmp/drawer_recipes_scroll.png");

		ws.close();
	} finally {
		chrome.kill();
	}
}

main().catch(err => {
	console.error(err);
	process.exit(1);
});

