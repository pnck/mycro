import { createEditor, languageMap, PrismEditor } from "prism-code-editor";
import { defaultCommands, editHistory } from "prism-code-editor/commands";
import { matchBrackets } from "prism-code-editor/match-brackets";
import { indentGuides } from "prism-code-editor/guides";
import { languages } from "prism-code-editor/prism";
import { insertText } from "prism-code-editor/utils";

import "prism-code-editor/layout.css";
import "prism-code-editor/scrollbar.css";
import "prism-code-editor/themes/github-light.css";
import "prism-code-editor/guides.css";
import "../styles/editor.css";

// Register MYCRO DSL v4 grammar rules per spec/dsl.md
languages["mycro"] = {
	directive: {
		pattern: /\\#!syntax:[^\n]+/i,
		greedy: true
	},
	comment: {
		pattern: /\\#.*$/m,
		greedy: true
	},
	escape: {
		pattern: /\\(?:[\\{}$]|[\r\n]+)/,
		greedy: true
	},
	parameter: {
		pattern: /#[1-9]/,
		greedy: true
	},
	variable: {
		pattern: /\$(?:\{(?:\s*slot\.[a-zA-Z0-9_.]+|\s*(?:ret|err|timeout|[a-zA-Z][a-zA-Z0-9_]*)\s*)\}|(?:slot\.[a-zA-Z0-9_.]+|ret|err|timeout|[a-zA-Z][a-zA-Z0-9_]*))/,
		greedy: true
	},
	"control-command": {
		pattern: /\\(?:rep|guarded|if|wait|delay|pace|def|use|call|all|any)\b/,
		greedy: true
	},
	"action-command": {
		pattern: /\\(?:click|move|mouse|mclick|mdown|mup|kdown|kup|text|endtext|pixelrect|row|rgb|typenum|enter|esc|space|tab|backspace|up|down|left|right|delete|home|end|pageup|pagedown|capslock|f[1-9]|f1[0-9]|f2[0-4]|media_[a-z_]+|volume_up|volume_down|mute)\b/,
		greedy: true
	},
	"combo-command": {
		pattern: /\\(?:ctrl|shift|alt|gui)(?:\+[a-zA-Z0-9]+)+/,
		greedy: true
	},
	command: {
		pattern: /\\[a-zA-Z][a-zA-Z0-9_]*/,
		greedy: true
	},
	dimension: {
		pattern: /[+-]?(?:\d+(?:\.\d+)?s|\d+ms)\b/,
		greedy: true
	},
	hex: {
		pattern: /[+-]?0x[0-9a-fA-F]+\b/i,
		greedy: true
	},
	number: {
		pattern: /[+-]?\b\d+\b/,
		greedy: true
	},
	operator: /[=<>!]+/,
	punctuation: /[{}[\](),]/
} as any;

languageMap["mycro"] = {
	comments: {
		line: "\\#"
	}
};

let macroEditorInstance: PrismEditor | null = null;

export function getMacroEditor(): PrismEditor | null {
	return macroEditorInstance;
}

/**
 * Inserts text at the current cursor position or replaces the active selection.
 * Keeps undo/redo history and restores focus to the editor.
 */
export function insertTextAtCursor(snippet: string): void {
	if (macroEditorInstance) {
		insertText(macroEditorInstance, snippet);
		macroEditorInstance.textarea.focus();
	} else {
		const codeEl = document.getElementById("macroCode") as HTMLTextAreaElement | null;
		if (!codeEl) return;
		const start = codeEl.selectionStart ?? codeEl.value.length;
		const end = codeEl.selectionEnd ?? codeEl.value.length;
		const val = codeEl.value;
		codeEl.value = val.slice(0, start) + snippet + val.slice(end);
		codeEl.selectionStart = codeEl.selectionEnd = start + snippet.length;
		codeEl.focus();
		codeEl.dispatchEvent(new Event("input", { bubbles: true }));
	}
}

/**
 * Replaces a specific character range [start, end] in the editor with replacement text,
 * positions the cursor at the end of the inserted replacement, and restores focus.
 */
export function replaceRangeInEditor(start: number, end: number, replacement: string): void {
	if (macroEditorInstance) {
		macroEditorInstance.textarea.setSelectionRange(start, end);
		insertText(macroEditorInstance, replacement);
		macroEditorInstance.textarea.focus();
	} else {
		const codeEl = document.getElementById("macroCode") as HTMLTextAreaElement | null;
		if (!codeEl) return;
		const val = codeEl.value;
		codeEl.value = val.slice(0, start) + replacement + val.slice(end);
		codeEl.selectionStart = codeEl.selectionEnd = start + replacement.length;
		codeEl.focus();
		codeEl.dispatchEvent(new Event("input", { bubbles: true }));
	}
}

export function initMacroEditor(): PrismEditor | null {
	const container = document.getElementById("macroEditorContainer");
	const codeEl = document.getElementById("macroCode") as HTMLTextAreaElement | null;
	if (!container || !codeEl) return null;

	const initialCode = codeEl.value || "";

	const editor = createEditor(
		container,
		{
			language: "mycro",
			tabSize: 2,
			insertSpaces: true,
			lineNumbers: true,
			wordWrap: true,
			value: initialCode,
			onUpdate(value) {
				if (codeEl.value !== value) {
					const desc = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value");
					desc?.set?.call(codeEl, value);
					codeEl.dispatchEvent(new Event("input", { bubbles: true }));
				}
			}
		},
		defaultCommands(),
		editHistory(),
		matchBrackets(),
		indentGuides()
	);

	macroEditorInstance = editor;
	(window as any).macroEditor = editor;

	// Intercept codeEl.value property so external reads/writes seamlessly sync with editor
	const desc = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value");
	Object.defineProperty(codeEl, "value", {
		get() {
			return editor.value;
		},
		set(newVal: string) {
			desc?.set?.call(codeEl, newVal);
			if (editor.value !== newVal) {
				editor.setOptions({ value: newVal });
			}
		},
		configurable: true
	});

	// Bind Ctrl+S / Cmd+S in the editor to save preset (NO execution shortcuts)
	editor.textarea.addEventListener("keydown", (e: KeyboardEvent) => {
		if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
			e.preventDefault();
			e.stopPropagation();
			const saveBtn = document.getElementById("macroSaveBtn") as HTMLButtonElement | null;
			if (saveBtn) {
				saveBtn.click();
			}
		}
	});

	return editor;
}

