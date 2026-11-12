#!/usr/bin/env node
import { spawn } from "node:child_process";

const CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9222;

async function sleep(ms) {
	return new Promise(r => setTimeout(r, ms));
}

async function main() {
	console.log("Starting headless Chrome on port 9222...");
	const chrome = spawn(CHROME_PATH, [
		"--headless=new",
		`--remote-debugging-port=${PORT}`,
		"--disable-gpu",
		"--no-sandbox",
		"about:blank"
	]);

	let exited = false;
	chrome.on("exit", (code) => {
		exited = true;
	});

	try {
		// Wait for CDP to be ready
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

		if (!targets || targets.length === 0) {
			throw new Error("Failed to connect to Chrome CDP");
		}

		const pageTarget = targets.find(t => t.type === "page") || targets[0];
		const wsUrl = pageTarget.webSocketDebuggerUrl;
		console.log("Connected to page WebSocket:", wsUrl);

		const ws = new WebSocket(wsUrl);
		await new Promise((res, rej) => {
			ws.onopen = res;
			ws.onerror = rej;
		});

		let idSeq = 1;
		const pending = new Map();
		ws.onmessage = (event) => {
			const data = JSON.parse(event.data);
			if (data.id && pending.has(data.id)) {
				const { resolve, reject } = pending.get(data.id);
				pending.delete(data.id);
				if (data.error) reject(new Error(JSON.stringify(data.error)));
				else resolve(data.result);
			}
		};

		function send(method, params = {}) {
			return new Promise((resolve, reject) => {
				const id = idSeq++;
				pending.set(id, { resolve, reject });
				ws.send(JSON.stringify({ id, method, params }));
			});
		}

		async function evaluate(expression) {
			const res = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
			if (res.exceptionDetails) {
				throw new Error("Evaluation failed: " + JSON.stringify(res.exceptionDetails));
			}
			return res.result.value;
		}

		await send("Page.enable");
		await send("Runtime.enable");

		console.log("Navigating to http://localhost:5173/ ...");
		await send("Page.navigate", { url: "http://localhost:5173/" });
		await sleep(800);

		console.log("\n--- TEST 1: Check Editor Mounted ---");
		const editorMounted = await evaluate(`!!document.querySelector('.prism-code-editor')`);
		console.log("Editor mounted in DOM:", editorMounted);
		if (!editorMounted) throw new Error("Editor not mounted!");

		console.log("\n--- TEST 2: Ctrl+S saves preset ---");
		// Set preset name and editor text
		await evaluate(`
			(() => {
				const nameInput = document.getElementById('macroPresetName');
				nameInput.value = 'my_test_preset';
				nameInput.dispatchEvent(new Event('input', { bubbles: true }));

				const editorEl = document.querySelector('.prism-code-editor');
				const textarea = editorEl.querySelector('textarea');
				textarea.focus();

				// Dispatch Ctrl+S
				const event = new KeyboardEvent('keydown', {
					key: 's',
					code: 'KeyS',
					ctrlKey: true,
					bubbles: true,
					cancelable: true
				});
				textarea.dispatchEvent(event);
			})()
		`);
		await sleep(300);

		const savedPresets = await evaluate(`localStorage.getItem('mycro_user_macros')`);
		console.log("LocalStorage after Ctrl+S:", savedPresets);
		if (!savedPresets || !savedPresets.includes("my_test_preset")) {
			throw new Error("Ctrl+S failed to save preset to localStorage!");
		}
		console.log("✓ Ctrl+S successfully saved preset!");

		console.log("\n--- TEST 3: Verify NO Execution Shortcut ---");
		const requestsFired = await evaluate(`
			(() => {
				let requestMade = false;
				const origFetch = window.fetch;
				window.fetch = (url, opts) => {
					if (typeof url === 'string' && url.includes('/macro')) {
						requestMade = true;
					}
					return origFetch(url, opts);
				};

				const textarea = document.querySelector('.prism-code-editor textarea');
				textarea.focus();

				// Try Ctrl+Enter
				textarea.dispatchEvent(new KeyboardEvent('keydown', {
					key: 'Enter',
					code: 'Enter',
					ctrlKey: true,
					bubbles: true
				}));

				// Try Cmd+Enter
				textarea.dispatchEvent(new KeyboardEvent('keydown', {
					key: 'Enter',
					code: 'Enter',
					metaKey: true,
					bubbles: true
				}));

				window.fetch = origFetch;
				return requestMade;
			})()
		`);
		console.log("Was macro execution triggered by Ctrl+Enter / Cmd+Enter:", requestsFired);
		if (requestsFired) {
			throw new Error("Macro run shortcut exists! Prohibited by user rule.");
		}
		console.log("✓ Confirmed: NO execution shortcut bound.");

		console.log("\n--- TEST 4: Pixel Assist snippet insertion at cursor ---");
		const snippetInsertionResult = await evaluate(`
			(() => {
				const textarea = document.querySelector('.prism-code-editor textarea');
				const codeEl = document.getElementById('macroCode');
				codeEl.value = "LINE_ONE\\nLINE_TWO";
				
				// Position cursor between lines
				textarea.focus();
				textarea.setSelectionRange(9, 9); // After "LINE_ONE\\n"
				textarea.dispatchEvent(new Event('selectionchange'));

				// Set snippet box value
				const paSnippet = document.getElementById('paSnippetBox');
				paSnippet.value = "\\\\pace{20ms}";

				// Click insert button
				const paInsertBtn = document.getElementById('paInsertSnippetBtn');
				paInsertBtn.click();

				return codeEl.value;
			})()
		`);
		console.log("Editor value after Pixel Assist insert at cursor:\n" + snippetInsertionResult);
		if (!snippetInsertionResult.includes("LINE_ONE\n\\pace{20ms}LINE_TWO")) {
			throw new Error("Pixel Assist failed to insert at cursor position! Got: " + snippetInsertionResult);
		}
		console.log("✓ Pixel Assist successfully inserted at cursor position!");

		console.log("\n--- TEST 5: Interactive Quick Manual Drawer ---");
		// Verify initial closed state
		const drawerInitial = await evaluate(`
			(() => {
				const drawer = document.getElementById('manualDrawer');
				return {
					hasOpenClass: drawer.classList.contains('open'),
					hasItems: drawer.querySelectorAll('.manual-item').length
				};
			})()
		`);
		console.log("Initial drawer state:", drawerInitial);
		if (drawerInitial.hasOpenClass || drawerInitial.hasItems === 0) {
			throw new Error("Drawer initial state invalid!");
		}

		// Open drawer via top button
		await evaluate(`document.getElementById('openManualBtn').click()`);
		await sleep(300);

		const drawerOpened = await evaluate(`
			(() => {
				const drawer = document.getElementById('manualDrawer');
				const backdrop = document.getElementById('manualDrawerBackdrop');
				return {
					drawerOpen: drawer.classList.contains('open'),
					backdropDisplay: window.getComputedStyle(backdrop).display
				};
			})()
		`);
		console.log("After clicking #openManualBtn:", drawerOpened);
		if (!drawerOpened.drawerOpen) {
			throw new Error("Drawer did not open on #openManualBtn click!");
		}

		// Search filtering in drawer
		const searchResult = await evaluate(`
			(() => {
				const input = document.getElementById('manualSearchInput');
				input.value = "guarded";
				input.dispatchEvent(new Event('input', { bubbles: true }));

				const visibleItems = Array.from(document.querySelectorAll('.manual-item'))
					.filter(el => el.style.display !== 'none');
				return visibleItems.map(el => el.querySelector('.manual-item-desc')?.textContent);
			})()
		`);
		console.log("Search 'guarded' returned items:", searchResult);
		if (searchResult.length === 0 || !searchResult.some(s => s && s.includes("Guarded"))) {
			throw new Error("Search in manual drawer failed to filter correctly!");
		}

		// Click to insert snippet from drawer
		const insertSnippetResult = await evaluate(`
			(() => {
				// Clear editor
				const codeEl = document.getElementById('macroCode');
				codeEl.value = "";

				const textarea = document.querySelector('.prism-code-editor textarea');
				textarea.focus();
				textarea.setSelectionRange(0, 0);

				// Find visible snippet button in drawer
				const guardedBtn = document.querySelector('.manual-item:not([style*="display: none"]) [data-snippet]');
				const snippetAttr = guardedBtn.getAttribute('data-snippet');
				guardedBtn.click();

				return {
					snippetAttr,
					editorValue: codeEl.value,
					hasInsertedClass: guardedBtn.classList.contains('inserted')
				};
			})()
		`);
		console.log("Clicked snippet in manual drawer:", insertSnippetResult);
		if (!insertSnippetResult.editorValue.includes(insertSnippetResult.snippetAttr.trim())) {
			throw new Error("Clicking snippet in manual drawer failed to insert text!");
		}
		console.log("✓ Drawer snippet insertion at cursor passed with visual feedback!");

		// Persistent: Escape key does NOT close drawer
		await evaluate(`
			window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		`);
		await sleep(100);

		const stillOpen = await evaluate(`document.getElementById('manualDrawer').classList.contains('open')`);
		console.log("Drawer still open after Escape key (persistent docking):", stillOpen);
		if (!stillOpen) {
			throw new Error("Escape key unexpectedly closed persistent drawer!");
		}

		// Close via close button ✕
		await evaluate(`document.getElementById('closeManualBtn').click()`);
		await sleep(300);
		const closedViaBtn = await evaluate(`!document.getElementById('manualDrawer').classList.contains('open')`);
		console.log("Drawer closed via close button ✕:", closedViaBtn);
		if (!closedViaBtn) throw new Error("Close button failed to close drawer!");

		// Open via floating button
		await evaluate(`document.getElementById('floatingManualBtn').click()`);
		await sleep(100);
		const reopenedFloating = await evaluate(`document.getElementById('manualDrawer').classList.contains('open')`);
		console.log("Drawer reopened via floating button:", reopenedFloating);
		if (!reopenedFloating) throw new Error("Floating button failed to open drawer!");

		// Close via close button ✕
		await evaluate(`document.getElementById('closeManualBtn').click()`);
		await sleep(300);
		const closedAgain = await evaluate(`!document.getElementById('manualDrawer').classList.contains('open')`);
		if (!closedAgain) throw new Error("Close button failed to close drawer!");

		console.log("\n========================================");
		console.log("ALL 4 USER DIRECTIVES VERIFIED IN REAL HEADLESS CHROME!");
		console.log("========================================");

		ws.close();
	} finally {
		if (!exited) {
			chrome.kill();
		}
	}
}

main().catch(err => {
	console.error("E2E Test Failed:", err);
	process.exit(1);
});

