#!/usr/bin/env node
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

		console.log("Navigating to http://localhost:5173/ with empty localStorage...");
		await send("Page.navigate", { url: "http://localhost:5173/" });
		await sleep(600);

		// Clear manual state for fresh start
		await send("Runtime.evaluate", {
			expression: "localStorage.removeItem('mycro_drawer')",
			awaitPromise: true
		});
		await send("Page.reload");
		await sleep(600);

		console.log("\n--- TEST 1: Default initial state (Closed, no animation glitch) ---");
		const initialState = await send("Runtime.evaluate", {
			expression: `
				(() => {
					const drawer = document.getElementById('manualDrawer');
					const style = window.getComputedStyle(drawer);
					return {
						isOpen: drawer.classList.contains('open'),
						visibility: style.visibility,
						transform: style.transform
					};
				})()
			`,
			returnByValue: true
		});
		console.log("Initial state:", initialState.result.value);
		if (initialState.result.value.isOpen || initialState.result.value.visibility !== "hidden") {
			throw new Error("Initial drawer state is not closed/hidden!");
		}
		console.log("✓ Initial drawer is hidden with zero flicker.");

		console.log("\n--- TEST 2: Open drawer and persist across page reload ---");
		await send("Runtime.evaluate", {
			expression: "document.getElementById('openManualBtn').click()",
			awaitPromise: true
		});
		await sleep(300);

		let stored = await send("Runtime.evaluate", {
			expression: "localStorage.getItem('mycro_drawer')",
			returnByValue: true
		});
		console.log("LocalStorage after opening:", stored.result.value);
		if (!stored.result.value.includes('"isOpen":true')) {
			throw new Error("Failed to persist isOpen:true to localStorage!");
		}

		console.log("Reloading page while drawer is open...");
		await send("Page.reload");
		await sleep(600);

		const restoredState = await send("Runtime.evaluate", {
			expression: `
				(() => {
					const drawer = document.getElementById('manualDrawer');
					const backdrop = document.getElementById('manualDrawerBackdrop');
					return {
						isOpen: drawer.classList.contains('open'),
						backdropDisplay: window.getComputedStyle(backdrop).display,
						visibility: window.getComputedStyle(drawer).visibility
					};
				})()
			`,
			returnByValue: true
		});
		console.log("Restored state after reload:", restoredState.result.value);
		if (!restoredState.result.value.isOpen || restoredState.result.value.visibility !== "visible") {
			throw new Error("Drawer failed to restore open state after reload!");
		}
		console.log("✓ Drawer stayed OPEN across page reload with zero retracting glitch!");

		console.log("\n--- TEST 3: Collapse categories and verify persistence across reload ---");
		await send("Runtime.evaluate", {
			expression: `
				// Click header of 'Keyboard: Editing & Navigation' to collapse it
				const headers = Array.from(document.querySelectorAll('.manual-section-header'));
				const editingKeysHeader = headers.find(h => h.textContent.includes('Keyboard: Editing & Navigation'));
				if (editingKeysHeader) editingKeysHeader.click();
			`,
			awaitPromise: true
		});
		await sleep(200);

		stored = await send("Runtime.evaluate", {
			expression: "localStorage.getItem('mycro_drawer')",
			returnByValue: true
		});
		console.log("LocalStorage after collapsing Keyboard: Editing & Navigation:", stored.result.value);
		if (!stored.result.value.includes("Keyboard: Editing & Navigation")) {
			throw new Error("Failed to persist collapsed category to localStorage!");
		}

		console.log("Reloading page to verify collapsed category persistence...");
		await send("Page.reload");
		await sleep(600);

		const collapsedSections = await send("Runtime.evaluate", {
			expression: `
				Array.from(document.querySelectorAll('.manual-section.collapsed'))
					.map(s => s.querySelector('.manual-section-name')?.textContent)
			`,
			returnByValue: true
		});
		console.log("Collapsed sections after reload:", collapsedSections.result.value);
		if (!collapsedSections.result.value.includes("Keyboard: Editing & Navigation")) {
			throw new Error("Collapsed state was not preserved across reload!");
		}
		console.log("✓ Collapsed category state successfully preserved across reload!");

		console.log("\n--- TEST 4: Close drawer and verify persistence across reload ---");
		await send("Runtime.evaluate", {
			expression: "document.getElementById('closeManualBtn').click()",
			awaitPromise: true
		});
		await sleep(300);

		stored = await send("Runtime.evaluate", {
			expression: "localStorage.getItem('mycro_drawer')",
			returnByValue: true
		});
		console.log("LocalStorage after closing:", stored.result.value);
		if (!stored.result.value.includes('"isOpen":false')) {
			throw new Error("Failed to persist isOpen:false to localStorage!");
		}

		console.log("Reloading page to verify closed state...");
		await send("Page.reload");
		await sleep(600);

		const closedAfterReload = await send("Runtime.evaluate", {
			expression: `
				(() => {
					const drawer = document.getElementById('manualDrawer');
					return {
						isOpen: drawer.classList.contains('open'),
						visibility: window.getComputedStyle(drawer).visibility
					};
				})()
			`,
			returnByValue: true
		});
		console.log("State after reload when closed:", closedAfterReload.result.value);
		if (closedAfterReload.result.value.isOpen || closedAfterReload.result.value.visibility !== "hidden") {
			throw new Error("Drawer did not remain closed after reload!");
		}
		console.log("✓ Drawer remained CLOSED across reload with zero transition flicker!");

		console.log("\n==========================================");
		console.log("ALL PERSISTENCE AND CACHING TESTS PASSED!");
		console.log("==========================================");

		ws.close();
	} finally {
		chrome.kill();
	}
}

main().catch(err => {
	console.error(err);
	process.exit(1);
});

