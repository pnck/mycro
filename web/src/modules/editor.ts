import { createEditor, languageMap, PrismEditor } from "prism-code-editor";
import { indentGuides } from "prism-code-editor/guides";
import { languages } from "prism-code-editor/prism";
import { isInsideVerbatim } from "./token-detector";
import { formatMacroCode } from "./formatter";
import { workspaceStore } from "../store";

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
		pattern: /\\(?:click|move|mdown|mup|kdown|kup|text|endtext|pixelrect|row|rgb|typenum|enter|esc|space|tab|backspace|up|down|left|right|delete|home|end|pageup|pagedown|capslock|f[1-9]|f1[0-9]|f2[0-4]|media_[a-z_]+|volup|voldn|mute)\b/,
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

// Lightweight Undo / Redo History Stack
interface HistoryState {
	val: string;
	sel: [number, number];
}

class HistoryManager {
	private stack: HistoryState[] = [];
	private index = -1;
	private timer: any = null;
	private isNavigating = false;

	init(val: string) {
		this.stack = [{ val, sel: [0, 0] }];
		this.index = 0;
	}

	push(force = false) {
		if (this.isNavigating || !macroEditorInstance) return;
		const val = macroEditorInstance.value;
		const ta = macroEditorInstance.textarea;
		const sel: [number, number] = [ta.selectionStart, ta.selectionEnd];

		if (this.stack[this.index] && this.stack[this.index].val === val) {
			this.stack[this.index].sel = sel;
			return;
		}

		const add = () => {
			this.stack = this.stack.slice(0, this.index + 1);
			this.stack.push({ val, sel });
			if (this.stack.length > 100) this.stack.shift();
			else this.index++;
		};

		if (force) {
			clearTimeout(this.timer);
			add();
		} else {
			clearTimeout(this.timer);
			this.timer = setTimeout(add, 300);
		}
	}

	undo() {
		if (this.index > 0) {
			clearTimeout(this.timer);
			this.index--;
			this.restore(this.stack[this.index]);
		}
	}

	redo() {
		if (this.index < this.stack.length - 1) {
			clearTimeout(this.timer);
			this.index++;
			this.restore(this.stack[this.index]);
		}
	}

	private restore(st: HistoryState) {
		if (!macroEditorInstance) return;
		this.isNavigating = true;
		const ta = macroEditorInstance.textarea;
		ta.value = st.val;
		macroEditorInstance.setOptions({ value: st.val });
		ta.setSelectionRange(st.sel[0], st.sel[1]);
		ta.dispatchEvent(new Event("input", { bubbles: true }));
		this.isNavigating = false;
	}
}

const history = new HistoryManager();

export function getMacroEditor(): PrismEditor | null {
	return macroEditorInstance;
}

/**
 * Inserts text at the current cursor position or replaces the active selection.
 * Keeps undo/redo history and restores focus to the editor.
 */
export function insertTextAtCursor(snippet: string): void {
	if (macroEditorInstance) {
		const ta = macroEditorInstance.textarea;
		ta.focus();
		ta.setRangeText(snippet, ta.selectionStart, ta.selectionEnd, "end");
		ta.dispatchEvent(new Event("input", { bubbles: true }));
		history.push(true);
	}
}

/**
 * Replaces a specific character range [start, end] in the editor with replacement text,
 * positions the cursor at the end of the inserted replacement, and restores focus.
 */
export function replaceRangeInEditor(start: number, end: number, replacement: string): void {
	if (macroEditorInstance) {
		const ta = macroEditorInstance.textarea;
		ta.focus();
		ta.setRangeText(replacement, start, end, "end");
		ta.dispatchEvent(new Event("input", { bubbles: true }));
		history.push(true);
	}
}

function handleTab(editor: PrismEditor, isShift: boolean) {
	const ta = editor.textarea;
	const val = editor.value;
	const start = ta.selectionStart;
	const end = ta.selectionEnd;

	// Shift+Tab: outdent all affected lines directly from line start, regardless of cursor position
	if (isShift) {
		const lineStart = val.lastIndexOf("\n", start - 1) + 1;
		const lineEnd = val.indexOf("\n", end) === -1 ? val.length : val.indexOf("\n", end);
		const text = val.slice(lineStart, lineEnd);
		const lines = text.split("\n");
		let startDelta = 0;
		let totalDelta = 0;

		const newLines = lines.map((l, i) => {
			let rem = 0;
			if (l.startsWith("  ")) rem = 2;
			else if (l.startsWith(" ") || l.startsWith("\t")) rem = 1;
			if (i === 0) startDelta = -rem;
			totalDelta -= rem;
			return l.slice(rem);
		});

		ta.setRangeText(newLines.join("\n"), lineStart, lineEnd, "select");
		ta.setSelectionRange(Math.max(lineStart, start + startDelta), Math.max(lineStart, end + totalDelta));
		ta.dispatchEvent(new Event("input", { bubbles: true }));
		return;
	}

	// Normal Tab (Indent): multiline selection indents each line; collapsed/single inserts 2 spaces
	if (start !== end && val.slice(start, end).includes("\n")) {
		const lineStart = val.lastIndexOf("\n", start - 1) + 1;
		const lineEnd = val.indexOf("\n", end) === -1 ? val.length : val.indexOf("\n", end);
		const text = val.slice(lineStart, lineEnd);
		const lines = text.split("\n");
		const newLines = lines.map(l => "  " + l);

		ta.setRangeText(newLines.join("\n"), lineStart, lineEnd, "select");
		ta.setSelectionRange(start + 2, end + lines.length * 2);
		ta.dispatchEvent(new Event("input", { bubbles: true }));
		return;
	}

	ta.setRangeText("  ", start, end, "end");
	ta.dispatchEvent(new Event("input", { bubbles: true }));
}

function handleEnter(editor: PrismEditor) {
	const ta = editor.textarea;
	const val = editor.value;
	const start = ta.selectionStart;
	const end = ta.selectionEnd;
	const lineStart = val.lastIndexOf("\n", start - 1) + 1;
	const before = val.slice(lineStart, start);
	const prevChar = val[start - 1];
	const nextChar = val[start];

	const indentMatch = before.match(/^[ \t]*/);
	const indent = indentMatch ? indentMatch[0] : "";

	// Enter between closed pairs: { } or [ ]
	if ((prevChar === "{" && nextChar === "}") || (prevChar === "[" && nextChar === "]")) {
		const innerIndent = indent + "  ";
		const insertion = "\n" + innerIndent + "\n" + indent;
		ta.setRangeText(insertion, start, end, "end");
		const targetPos = start + 1 + innerIndent.length;
		ta.setSelectionRange(targetPos, targetPos);
		ta.dispatchEvent(new Event("input", { bubbles: true }));
		return;
	}

	// Standard auto-indent: inherit indent, +2 if line ends with {
	const extra = /\{\s*$/.test(before) ? "  " : "";
	const insertion = "\n" + indent + extra;
	ta.setRangeText(insertion, start, end, "end");
	ta.dispatchEvent(new Event("input", { bubbles: true }));
}

function handleBracketPair(editor: PrismEditor, openChar: "{" | "["): boolean {
	const ta = editor.textarea;
	const val = editor.value;
	const start = ta.selectionStart;
	const end = ta.selectionEnd;

	if (isInsideVerbatim(val, start)) {
		return false;
	}

	const closeChar = openChar === "{" ? "}" : "]";
	if (start !== end) {
		const selected = val.slice(start, end);
		ta.setRangeText(openChar + selected + closeChar, start, end, "select");
		ta.setSelectionRange(start + 1, end + 1);
	} else {
		ta.setRangeText(openChar + closeChar, start, end, "end");
		ta.setSelectionRange(start + 1, start + 1);
	}
	ta.dispatchEvent(new Event("input", { bubbles: true }));
	return true;
}

function handleToggleComment(editor: PrismEditor) {
	const ta = editor.textarea;
	const val = editor.value;
	const start = ta.selectionStart;
	const end = ta.selectionEnd;
	const lineStart = val.lastIndexOf("\n", start - 1) + 1;
	const lineEnd = val.indexOf("\n", end) === -1 ? val.length : val.indexOf("\n", end);
	const text = val.slice(lineStart, lineEnd);
	const lines = text.split("\n");
	const nonEmpty = lines.filter(l => l.trim().length > 0);
	const allCommented = nonEmpty.length > 0 && nonEmpty.every(l => /^[ \t]*\\#/.test(l));

	const newLines = allCommented
		? lines.map(l => l.replace(/^([ \t]*)\\# ?/, "$1"))
		: lines.map(l => l.replace(/^([ \t]*)/, "$1\\# "));

	ta.setRangeText(newLines.join("\n"), lineStart, lineEnd, "select");
	ta.dispatchEvent(new Event("input", { bubbles: true }));
}

export function initMacroEditor(): PrismEditor | null {
	const container = document.getElementById("macroEditorContainer");
	const codeEl = document.getElementById("macroCode") as HTMLTextAreaElement | null;
	if (!container || !codeEl) return null;

	const initialCode = codeEl.value || workspaceStore.get() || "";
	codeEl.value = initialCode;

	const protoDesc = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value");

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
					protoDesc?.set?.call(codeEl, value);
					codeEl.dispatchEvent(new Event("input", { bubbles: true }));
				}
				workspaceStore.set(value);
				history.push(false);
			}
		},
		indentGuides()
	);

	macroEditorInstance = editor;
	(window as any).macroEditor = editor;
	history.init(initialCode);

	// Intercept codeEl.value property so external reads/writes seamlessly sync with editor
	Object.defineProperty(codeEl, "value", {
		get() {
			return editor.value;
		},
		set(newVal: string) {
			protoDesc?.set?.call(codeEl, newVal);
			if (editor.value !== newVal) {
				editor.setOptions({ value: newVal });
			}
			workspaceStore.set(newVal);
			history.push(true);
		},
		configurable: true
	});

	function triggerFormat() {
		const current = editor.value;
		const formatted = formatMacroCode(current);
		if (formatted && formatted !== current) {
			editor.textarea.value = formatted;
			editor.setOptions({ value: formatted });
			editor.textarea.dispatchEvent(new Event("input", { bubbles: true }));
			history.push(true);
		}
	}

	const formatBtn = document.getElementById("macroFormatBtn") as HTMLButtonElement | null;
	if (formatBtn) {
		formatBtn.addEventListener("click", triggerFormat);
	}

	// Essential editor shortcut bindings
	editor.textarea.addEventListener("keydown", (e: KeyboardEvent) => {
		const isMod = e.ctrlKey || e.metaKey;
		const key = e.key.toLowerCase();

		// Save: Ctrl+S / Cmd+S
		if (isMod && key === "s") {
			e.preventDefault();
			e.stopPropagation();
			(document.getElementById("macroSaveBtn") as HTMLButtonElement | null)?.click();
			return;
		}

		// Format: Shift+Alt+F
		if (e.shiftKey && e.altKey && key === "f") {
			e.preventDefault();
			e.stopPropagation();
			triggerFormat();
			return;
		}

		// Undo: Ctrl+Z / Cmd+Z
		if (isMod && !e.shiftKey && key === "z") {
			e.preventDefault();
			history.undo();
			return;
		}

		// Redo: Ctrl+Y / Cmd+Shift+Z / Ctrl+Shift+Z
		if ((isMod && key === "y") || (isMod && e.shiftKey && key === "z")) {
			e.preventDefault();
			history.redo();
			return;
		}

		// Tab / Shift+Tab indent
		if (e.key === "Tab") {
			e.preventDefault();
			handleTab(editor, e.shiftKey);
			history.push(true);
			return;
		}

		// Enter auto-indent
		if (e.key === "Enter" && !e.shiftKey && !isMod && !e.altKey) {
			e.preventDefault();
			handleEnter(editor);
			history.push(true);
			return;
		}

		// Auto-close parameter and block brackets: {} and []
		if ((e.key === "{" || e.key === "[") && !isMod && !e.altKey) {
			if (handleBracketPair(editor, e.key)) {
				e.preventDefault();
				history.push(true);
				return;
			}
		}

		// Overtype closing bracket: step over } or ] if already directly following cursor
		if ((e.key === "}" || e.key === "]") && !isMod && !e.altKey) {
			const start = editor.textarea.selectionStart;
			const end = editor.textarea.selectionEnd;
			if (start === end && editor.value[start] === e.key && !isInsideVerbatim(editor.value, start)) {
				e.preventDefault();
				editor.textarea.setSelectionRange(start + 1, start + 1);
				return;
			}
		}

		// Backspace between empty pairs: delete both brackets
		if (e.key === "Backspace" && !isMod && !e.altKey) {
			const start = editor.textarea.selectionStart;
			const end = editor.textarea.selectionEnd;
			if (start === end && start > 0) {
				const val = editor.value;
				const prev = val[start - 1];
				const next = val[start];
				if (((prev === "{" && next === "}") || (prev === "[" && next === "]")) && !isInsideVerbatim(val, start)) {
					e.preventDefault();
					editor.textarea.setRangeText("", start - 1, start + 1, "end");
					editor.textarea.dispatchEvent(new Event("input", { bubbles: true }));
					history.push(true);
					return;
				}
			}
		}

		// Toggle Comment: Ctrl+/ or Cmd+/
		if (isMod && e.key === "/") {
			e.preventDefault();
			handleToggleComment(editor);
			history.push(true);
			return;
		}
	});

	return editor;
}


