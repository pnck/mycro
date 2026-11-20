import { getMacroEditor, replaceRangeInEditor } from "./editor";
import { escapeHtml, escapeAttr } from "./manual";
import {
	detectToken,
	checkBracketBalance,
	detectStructuralScope,
	scanBraces,
	type DetectionResult
} from "./token-detector";

let inspectorEls: {
	container: HTMLElement;
	scope: HTMLElement | null;
	bracket: HTMLElement | null;
	context: HTMLElement | null;
	token: HTMLElement | null;
	count: HTMLElement | null;
	body: HTMLElement | null;
} | null = null;

function getInspectorEls() {
	if (!inspectorEls) {
		const container = document.getElementById("manualLiveInspector");
		if (container) {
			inspectorEls = {
				container,
				scope: document.getElementById("inspectorScope"),
				bracket: document.getElementById("inspectorBracketStatus"),
				context: document.getElementById("inspectorContext"),
				token: document.getElementById("inspectorToken"),
				count: document.getElementById("inspectorMatchCount"),
				body: document.getElementById("inspectorCandidatesBody")
			};
		}
	}
	return inspectorEls;
}

export function initTokenInspector(): void {
	const editor = getMacroEditor();
	if (!editor) {
		setTimeout(initTokenInspector, 200);
		return;
	}

	const els = getInspectorEls();
	if (!els) return;

	updateTokenInspector();

	const textarea = editor.textarea;
	textarea.addEventListener("input", updateTokenInspector);
	textarea.addEventListener("keyup", updateTokenInspector);
	textarea.addEventListener("click", updateTokenInspector);
	textarea.addEventListener("focus", updateTokenInspector);
	document.addEventListener("selectionchange", () => {
		if (document.activeElement === textarea) {
			updateTokenInspector();
		}
	});

	// Handle candidate chip clicks
	els.container.addEventListener("click", (e: MouseEvent) => {
		const target = e.target as HTMLElement;
		const chip = target.closest("[data-insert]") as HTMLElement | null;
		if (!chip) return;

		const insertText = chip.getAttribute("data-insert");
		const startStr = chip.getAttribute("data-token-start");
		const endStr = chip.getAttribute("data-token-end");

		if (insertText === null || startStr === null || endStr === null) return;

		const start = parseInt(startStr, 10);
		const end = parseInt(endStr, 10);

		replaceRangeInEditor(start, end, insertText);

		// Flash chip feedback
		chip.classList.add("inserted");
		setTimeout(() => chip.classList.remove("inserted"), 500);

		// Re-evaluate inspector
		requestAnimationFrame(updateTokenInspector);
	});
}

export function updateTokenInspector(): void {
	const els = getInspectorEls();
	if (!els) return;

	const editor = getMacroEditor();
	if (!editor) return;

	const text = editor.value;
	const cursor = editor.textarea.selectionStart ?? text.length;

	// Single-pass lexical & structural scan for all inspector checks
	const scan = scanBraces(text, cursor);

	// 1. Bracket balance & depth check across entire document
	const bracketResult = checkBracketBalance(text, cursor, scan);
	if (els.bracket) {
		els.bracket.textContent = bracketResult.statusText;
		els.bracket.className = `inspector-bracket-status bracket-${bracketResult.statusType}`;
		els.bracket.title = `Bracket Balance: ${bracketResult.statusText}`;
	}

	// 2. Structural Scope Breadcrumb detection at cursor
	const scopeText = detectStructuralScope(text, cursor, scan);
	if (els.scope) {
		els.scope.textContent = scopeText;
		els.scope.title = `Structural Syntax Scope: ${scopeText}`;
	}

	// 3. Lexical Token Detection & Candidates
	const result = detectToken(text, cursor, scan);

	if (!result || result.matches.length === 0) {
		els.container.classList.remove("active");
		if (els.context) els.context.textContent = "Context: Text";
		if (els.token) els.token.textContent = "Ready";
		if (els.count) els.count.textContent = "";
		if (els.body) {
			els.body.innerHTML = `
				<span class="inspector-idle-guide">
					Live Inspector Ready: Type <code>\\</code> for commands/syntax, <code>$</code> for variables/slots, or edit inside <code>{...}</code>
				</span>
			`;
		}
		return;
	}

	els.container.classList.add("active");
	if (els.context) els.context.textContent = `Context: ${result.context}`;
	if (els.token) els.token.textContent = result.displayToken || result.rawToken;
	if (els.count) els.count.textContent = `${result.matches.length} matches`;

	if (els.body) {
		els.body.innerHTML = result.matches.map(c => `
			<button type="button" class="inspector-chip" data-insert="${escapeAttr(c.insert)}" data-token-start="${c.tokenStart ?? result.tokenStart}" data-token-end="${c.tokenEnd ?? result.tokenEnd}" title="${escapeAttr(c.desc)}">
				<span class="inspector-chip-kind kind-${c.kind}">${c.kind}</span>
				<span class="inspector-chip-label">${escapeHtml(c.label)}</span>
				<span class="inspector-chip-desc">${escapeHtml(c.desc)}</span>
			</button>
		`).join("");
	}
}


