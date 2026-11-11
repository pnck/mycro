import { getMacroEditor, replaceRangeInEditor } from "./editor";
import {
	detectToken,
	checkBracketBalance,
	detectStructuralScope,
	CandidateKind,
	InspectorCandidate,
	DetectionResult,
	BracketCheckResult,
	COMMAND_CANDIDATES,
	SYNTAX_CANDIDATES,
	ESCAPE_CANDIDATES,
	MOUSE_BUTTON_CANDIDATES,
	KEY_NAME_CANDIDATES,
	NAMESPACE_CANDIDATES,
	EXT_FUNCTION_CANDIDATES,
	PREDICATE_OP_CANDIDATES,
	PREDICATE_LOGIC_CANDIDATES
} from "./token-detector";

export type { CandidateKind, InspectorCandidate, DetectionResult, BracketCheckResult };
export {
	detectToken,
	checkBracketBalance,
	detectStructuralScope,
	COMMAND_CANDIDATES,
	SYNTAX_CANDIDATES,
	ESCAPE_CANDIDATES,
	MOUSE_BUTTON_CANDIDATES,
	KEY_NAME_CANDIDATES,
	NAMESPACE_CANDIDATES,
	EXT_FUNCTION_CANDIDATES,
	PREDICATE_OP_CANDIDATES,
	PREDICATE_LOGIC_CANDIDATES
};

export function initTokenInspector(): void {
	const editor = getMacroEditor();
	if (!editor) {
		setTimeout(initTokenInspector, 200);
		return;
	}

	const container = document.getElementById("manualLiveInspector");
	if (!container) return;

	// Attach detector and balance checker to window for debug / testing
	(window as any).detectToken = detectToken;
	(window as any).checkBracketBalance = checkBracketBalance;
	(window as any).detectStructuralScope = detectStructuralScope;

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
	container.addEventListener("click", (e: MouseEvent) => {
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
	const container = document.getElementById("manualLiveInspector");
	const scopeEl = document.getElementById("inspectorScope");
	const bracketEl = document.getElementById("inspectorBracketStatus");
	const contextEl = document.getElementById("inspectorContext");
	const tokenEl = document.getElementById("inspectorToken");
	const countEl = document.getElementById("inspectorMatchCount");
	const bodyEl = document.getElementById("inspectorCandidatesBody");

	if (!container) return;

	const editor = getMacroEditor();
	if (!editor) return;

	const text = editor.value;
	const cursor = editor.textarea.selectionStart ?? text.length;

	// 1. Bracket balance & depth check across entire document
	const bracketResult = checkBracketBalance(text, cursor);
	if (bracketEl) {
		bracketEl.textContent = bracketResult.statusText;
		bracketEl.className = `inspector-bracket-status bracket-${bracketResult.statusType}`;
		bracketEl.title = `Bracket Balance: ${bracketResult.statusText}`;
	}

	// 2. Structural Scope Breadcrumb detection at cursor
	const scopeText = detectStructuralScope(text, cursor);
	if (scopeEl) {
		scopeEl.textContent = scopeText;
		scopeEl.title = `Structural Syntax Scope: ${scopeText}`;
	}

	// 3. Lexical Token Detection & Candidates
	const result = detectToken(text, cursor);

	if (!result || result.matches.length === 0) {
		container.classList.remove("active");
		if (contextEl) contextEl.textContent = "Context: Text";
		if (tokenEl) tokenEl.textContent = "Ready";
		if (countEl) countEl.textContent = "";
		if (bodyEl) {
			bodyEl.innerHTML = `
				<span class="inspector-idle-guide">
					💡 Live Inspector Ready: Type <code>\\</code> for commands/syntax, <code>$</code> for variables/slots, or edit inside <code>{...}</code>
				</span>
			`;
		}
		return;
	}

	container.classList.add("active");
	if (contextEl) contextEl.textContent = `Context: ${result.context}`;
	if (tokenEl) tokenEl.textContent = result.displayToken || result.rawToken;
	if (countEl) countEl.textContent = `${result.matches.length} matches`;

	if (bodyEl) {
		bodyEl.innerHTML = result.matches.map(c => `
			<button type="button" class="inspector-chip" data-insert="${escapeAttr(c.insert)}" data-token-start="${c.tokenStart ?? result.tokenStart}" data-token-end="${c.tokenEnd ?? result.tokenEnd}" title="${escapeAttr(c.desc)}">
				<span class="inspector-chip-kind kind-${c.kind}">${c.kind}</span>
				<span class="inspector-chip-label">${escapeHtml(c.label)}</span>
				<span class="inspector-chip-desc">${escapeHtml(c.desc)}</span>
			</button>
		`).join("");
	}
}

function escapeHtml(str: string): string {
	return str
		.replace(/&/g, "&amp;")
		.replace(/</g, "&lt;")
		.replace(/>/g, "&gt;")
		.replace(/"/g, "&quot;")
		.replace(/'/g, "&#039;");
}

function escapeAttr(str: string): string {
	return str
		.replace(/&/g, "&amp;")
		.replace(/"/g, "&quot;")
		.replace(/'/g, "&#039;")
		.replace(/</g, "&lt;")
		.replace(/>/g, "&gt;");
}
