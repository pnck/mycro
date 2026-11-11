#!/usr/bin/env node
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9222;
const HTTP_PORT = 8089;

async function sleep(ms) {
	return new Promise(r => setTimeout(r, ms));
}

async function main() {
	// 1. Start simple static server for web/dist/index.html
	const html = readFileSync(join(process.cwd(), "web/dist/index.html"), "utf-8");
	const server = createServer((req, res) => {
		res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
		res.end(html);
	});
	server.listen(HTTP_PORT);
	console.log(`Static server running at http://127.0.0.1:${HTTP_PORT}`);

	// 2. Launch headless Chrome with temp user data dir
	const tmpDir = `/tmp/chrome-test-${Date.now()}`;
	const chrome = spawn(CHROME_PATH, [
		"--headless=new",
		`--remote-debugging-port=${PORT}`,
		`--user-data-dir=${tmpDir}`,
		"--window-size=1280,900",
		"--disable-gpu",
		"--no-sandbox",
		"about:blank"
	]);
	chrome.stderr.on("data", d => {
		// console.log("chrome err:", d.toString());
	});
	chrome.on("exit", code => {
		console.log("chrome exited with code:", code);
	});

	try {
		let targets = null;
		for (let i = 0; i < 30; i++) {
			await sleep(200);
			try {
				const res = await fetch(`http://127.0.0.1:${PORT}/json`);
				if (res.ok) {
					const list = await res.json();
					if (Array.isArray(list) && list.length > 0) {
						targets = list;
						console.log("Got targets:", targets.length);
						break;
					}
				}
			} catch (e) {
				// retry
			}
		}

		if (!targets || targets.length === 0) {
			const newRes = await fetch(`http://127.0.0.1:${PORT}/json/new`);
			if (newRes.ok) {
				const page = await newRes.json();
				targets = [page];
			}
		}

		if (!targets || targets.length === 0) {
			throw new Error("Could not connect to Chrome DevTools targets!");
		}

		const pageTarget = targets.find(t => t.type === "page") || targets[0];
		const ws = new WebSocket(pageTarget.webSocketDebuggerUrl);
		await new Promise(r => ws.onopen = r);

		let idSeq = 1;
		const pending = new Map();
		ws.onmessage = e => {
			const d = JSON.parse(e.data);
			if (d.id && pending.has(d.id)) {
				const { resolve, reject } = pending.get(d.id);
				pending.delete(d.id);
				if (d.error) reject(new Error(JSON.stringify(d.error)));
				else resolve(d.result);
			}
		};

		const send = (method, params = {}) => new Promise((resolve, reject) => {
			const id = idSeq++;
			pending.set(id, { resolve, reject });
			ws.send(JSON.stringify({ id, method, params }));
		});

		const evaluate = async (expression) => {
			const res = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
			if (res.exceptionDetails) {
				throw new Error("Eval error: " + JSON.stringify(res.exceptionDetails));
			}
			return res.result.value;
		};

		await send("Page.enable");
		await send("Runtime.enable");

		console.log("Navigating to test app...");
		await send("Page.navigate", { url: `http://127.0.0.1:${HTTP_PORT}/` });
		await sleep(600);

		console.log("\n--- TEST 1: Open Manual Drawer & Check Backdrop ---");
		await evaluate(`document.getElementById('openManualBtn').click()`);
		await sleep(200);

		const drawerStatus = await evaluate(`
			(() => {
				const drawer = document.getElementById('manualDrawer');
				const backdrop = document.getElementById('manualDrawerBackdrop');
				const backdropStyle = window.getComputedStyle(backdrop);
				return {
					isOpen: drawer.classList.contains('open'),
					backdropDisplay: backdropStyle.display,
					backdropPointerEvents: backdropStyle.pointerEvents
				};
			})()
		`);
		console.log("Drawer open & backdrop status:", drawerStatus);
		if (!drawerStatus.isOpen) throw new Error("Drawer is not open!");
		if (drawerStatus.backdropPointerEvents !== "none") throw new Error("Backdrop must have pointer-events: none!");
		console.log("✓ Requirement 1a: Backdrop is non-blocking with pointer-events: none.");

		console.log("\n--- TEST 2: Persistent: click outside & Escape do not close ---");
		await evaluate(`
			// Dispatch click on body
			document.body.dispatchEvent(new MouseEvent('click', { bubbles: true }));
			// Dispatch Escape key
			document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		`);
		await sleep(200);
		const stillOpen = await evaluate(`document.getElementById('manualDrawer').classList.contains('open')`);
		if (!stillOpen) throw new Error("Drawer closed on click-outside or Escape!");
		console.log("✓ Requirement 1b: Drawer persists and does not close on outside click or Escape.");

		console.log("\n--- TEST 3: Zero Copy Buttons in Manual Drawer ---");
		const copyButtonsInfo = await evaluate(`
			(() => {
				const drawer = document.getElementById('manualDrawer');
				const drawerCopyBtns = Array.from(drawer.querySelectorAll('.manual-copy-btn, button[title*="Copy"], button[aria-label*="Copy"]'));
				const allCopyBtns = Array.from(document.querySelectorAll('.manual-copy-btn, button[title*="Copy"], button[aria-label*="Copy"]'));
				return {
					inDrawer: drawerCopyBtns.map(b => ({ tag: b.tagName, cls: b.className, title: b.title, text: b.textContent })),
					all: allCopyBtns.map(b => ({ tag: b.tagName, cls: b.className, title: b.title, text: b.textContent, inDrawer: drawer.contains(b) }))
				};
			})()
		`);
		console.log("Copy buttons info:", copyButtonsInfo);
		if (copyButtonsInfo.inDrawer.length !== 0) throw new Error(`Found ${copyButtonsInfo.inDrawer.length} copy buttons in drawer! All copy buttons must be removed.`);
		console.log("✓ Requirement 2: All copy buttons successfully removed from Manual Drawer; only ➕ insert buttons remain.");

		console.log("\n--- TEST 4: Interactive Snippet Assembly Form ---");
		const formElements = await evaluate(`
			(() => {
				const selects = document.querySelectorAll('.manual-assemble-row select');
				const inputs = document.querySelectorAll('.manual-assemble-row input');
				const insertBtns = document.querySelectorAll('.manual-assemble-row .manual-insert-btn');
				return {
					selectCount: selects.length,
					inputCount: inputs.length,
					insertBtnCount: insertBtns.length
				};
			})()
		`);
		console.log("Assemble row form controls:", formElements);
		if (formElements.selectCount === 0 || formElements.inputCount === 0 || formElements.insertBtnCount === 0) {
			throw new Error("Interactive assemble controls missing!");
		}

		// Clear editor, configure an assemble form row, and click insert
		const insertedText = await evaluate(`
			(() => {
				const editor = window.macroEditor;
				editor.value = '';
				
				const rows = Array.from(document.querySelectorAll('.manual-assemble-row'));
				const rowWithInput = rows.find(r => r.querySelector('input') !== null);
				if (!rowWithInput) throw new Error("No assemble row with input found");
				
				const input = rowWithInput.querySelector('input');
				input.value = '250';
				input.dispatchEvent(new Event('input', { bubbles: true }));
				
				const btn = rowWithInput.querySelector('.manual-insert-btn');
				btn.click();
				return editor.value;
			})()
		`);
		console.log("Editor content after assembly insert:", JSON.stringify(insertedText));
		if (!insertedText || insertedText.length === 0) {
			throw new Error("Expected text to be inserted into editor!");
		}
		console.log("✓ Requirement 3: Interactive snippet assembly dynamically constructs and inserts snippet.");

		console.log("\n--- TEST 5: Live Token Inspector & Candidates Dictionary ---");
		const inspectorStatus = await evaluate(`
			(() => {
				const inspector = document.getElementById('manualLiveInspector');
				const editor = window.macroEditor;
				editor.value = '\\\\';
				editor.textarea.setSelectionRange(1, 1);
				editor.textarea.dispatchEvent(new Event('input', { bubbles: true }));
				document.dispatchEvent(new Event('selectionchange', { bubbles: true }));
				
				const context = document.getElementById('inspectorContext')?.textContent;
				const token = document.getElementById('inspectorToken')?.textContent;
				const count = document.getElementById('inspectorMatchCount')?.textContent;
				const chips = Array.from(inspector.querySelectorAll('.inspector-chip')).map(c => c.textContent.trim());
				return { context, token, count, chipCount: chips.length, sampleChips: chips.slice(0, 5) };
			})()
		`);
		console.log("Inspector output for '\\':", inspectorStatus);
		if (inspectorStatus.chipCount === 0 || !inspectorStatus.token || !inspectorStatus.token.includes('\\')) {
			throw new Error("Inspector failed to detect command prefix '\\'!");
		}

		// Helper function for interactive inspector evaluation
		async function testInspectorScenario(desc, inputCode, cursorOffset, expectedContext, expectedSnippet, chipLabelToClick, expectedAfterReplace) {
			console.log(`Checking ${desc}...`);
			const res = await evaluate(`
				(() => {
					const inspector = document.getElementById('manualLiveInspector');
					const editor = window.macroEditor;
					editor.setOptions({ value: ${JSON.stringify(inputCode)} });
					const pos = ${cursorOffset !== undefined ? cursorOffset : inputCode.length};
					editor.textarea.setSelectionRange(pos, pos);
					editor.textarea.dispatchEvent(new Event('input', { bubbles: true }));
					document.dispatchEvent(new Event('selectionchange', { bubbles: true }));

					const context = document.getElementById('inspectorContext')?.textContent || '';
					const chips = Array.from(inspector.querySelectorAll('.inspector-chip'));
					const labels = chips.map(c => c.querySelector('.inspector-chip-label')?.textContent?.trim() || '');
					
					let replacedValue = null;
					if (${JSON.stringify(chipLabelToClick)}) {
						const chip = chips.find(c => c.querySelector('.inspector-chip-label')?.textContent?.trim() === ${JSON.stringify(chipLabelToClick)});
						if (chip) {
							chip.click();
							replacedValue = editor.value;
						}
					}

					return { context, labels, replacedValue };
				})()
			`);

			if (!res.context.includes(expectedContext)) {
				throw new Error(`${desc} expected context '${expectedContext}', got '${res.context}'`);
			}
			if (expectedSnippet && !res.labels.some(l => l.includes(expectedSnippet))) {
				throw new Error(`${desc} expected label snippet '${expectedSnippet}' in [${res.labels.slice(0, 8).join(', ')}]`);
			}
			if (expectedAfterReplace !== undefined) {
				if (res.replacedValue !== expectedAfterReplace) {
					throw new Error(`${desc} expected replaced text ${JSON.stringify(expectedAfterReplace)}, got ${JSON.stringify(res.replacedValue)}`);
				}
			}
			console.log(`✓ ${desc} passed!`);
		}

		// Test 5a: \# matches comment & syntax directives, click cleanly replaces
		await testInspectorScenario(
			"Line start '\\#'",
			"\\#",
			2,
			"Comment / Directive",
			"\\#!syntax: free",
			"\\#!syntax: free",
			"\\#!syntax: free\n"
		);

		// Test 5b: \#! matches syntax directives
		await testInspectorScenario(
			"Directive prefix '\\#!'",
			"\\#!",
			3,
			"Syntax Directive",
			"\\#!syntax: typewriter",
			"\\#!syntax: typewriter",
			"\\#!syntax: typewriter\n"
		);

		// Test 5c: \#!syn matches syntax directives and cleanly replaces without duplicates
		await testInspectorScenario(
			"Directive partial '\\#!syn'",
			"\\#!syn",
			6,
			"Syntax Directive",
			"\\#!syntax: free",
			"\\#!syntax: free",
			"\\#!syntax: free\n"
		);

		// Test 5d: \set matches command \set
		await testInspectorScenario(
			"Command '\\set'",
			"\\set",
			4,
			"Command / Key",
			"\\set",
			"\\set",
			"\\set{x}{100}"
		);

		// Test 5e: $ matches variable candidates
		await testInspectorScenario(
			"Variable prefix '$'",
			"$",
			1,
			"Variable / Slot",
			"$ret",
			"$ret",
			"$ret"
		);

		// Test 5f: $ret matches $ret
		await testInspectorScenario(
			"Variable '$ret'",
			"$ret",
			4,
			"Variable / Slot",
			"$ret",
			"$ret",
			"$ret"
		);

		// Test 5g: \click{ matches mouse buttons
		await testInspectorScenario(
			"Brace context '\\click{'",
			"\\click{",
			7,
			"Mouse Button",
			"L",
			"L",
			"\\click{L"
		);

		// Test 5h: \use{ matches namespaces
		await testInspectorScenario(
			"Brace context '\\use{'",
			"\\use{",
			5,
			"Namespace",
			"sys",
			"sys",
			"\\use{sys"
		);

		// Test 5i: \if{ {$x, matches comparison operators
		await testInspectorScenario(
			"Brace context '\\if{ {$x, '",
			"\\if{ {$x, ",
			10,
			"Predicate Operator",
			">=",
			">=",
			"\\if{ {$x, >="
		);

		console.log("\n=================================");
		console.log("ALL REQUIREMENTS VERIFIED IN E2E!");
		console.log("=================================");

	} finally {
		chrome.kill();
		server.close();
	}
}

main().catch(err => {
	console.error(err);
	process.exit(1);
});
