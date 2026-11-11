export type CandidateKind =
	| "cmd"
	| "key"
	| "var"
	| "slot"
	| "btn"
	| "ns"
	| "macro"
	| "syntax"
	| "escape"
	| "op";

export interface InspectorCandidate {
	label: string;
	insert: string;
	kind: CandidateKind;
	desc: string;
	tokenStart?: number;
	tokenEnd?: number;
}

// 1. Static built-in commands dictionary per spec/dsl.md
export const COMMAND_CANDIDATES: InspectorCandidate[] = [
	{ label: "\\set", insert: "\\set{x}{100}", kind: "cmd", desc: "Declare & assign variable" },
	{ label: "\\add", insert: "\\add{x}{1}", kind: "cmd", desc: "In-place addition" },
	{ label: "\\sub", insert: "\\sub{x}{1}", kind: "cmd", desc: "In-place subtraction" },
	{ label: "\\click", insert: "\\click{L}", kind: "cmd", desc: "Click mouse button [L/R/M/B/F]" },
	{ label: "\\move", insert: "\\move{100, -50}", kind: "cmd", desc: "Relative mouse movement dx, dy" },
	{ label: "\\mdown", insert: "\\mdown{L}", kind: "cmd", desc: "Press and hold mouse button" },
	{ label: "\\mup", insert: "\\mup{L}", kind: "cmd", desc: "Release held mouse button" },
	{ label: "\\delay", insert: "\\delay{100ms}", kind: "cmd", desc: "Explicit sleep duration" },
	{ label: "\\pace", insert: "\\pace{20ms}", kind: "cmd", desc: "Set VM inter-action pacing" },
	{ label: "\\rep", insert: "\\rep{10}{\n  \\click{L}\n  \\delay{50ms}\n}\n", kind: "cmd", desc: "Bounded repeat loop" },
	{ label: "\\if", insert: "\\if{ {$x, >=, 0} }{\n  \\enter\n}\n", kind: "cmd", desc: "Conditional branch" },
	{ label: "\\all", insert: "\\all{ {$x, >=, 0}, {$x, <=, 100} }", kind: "cmd", desc: "Conjunction AND logic block" },
	{ label: "\\any", insert: "\\any{ {$err, !=, 0}, {$timeout, =, 1} }", kind: "cmd", desc: "Disjunction OR logic block" },
	{ label: "\\guarded", insert: "\\guarded{ {$err, =, 0} }{\n  \\ctrl+c\n}{\n  \\esc\n}\n", kind: "cmd", desc: "Guarded protected execution block" },
	{ label: "\\wait", insert: "\\wait[5s]{ $slot.net.msg.val }", kind: "cmd", desc: "Wait for signal arrival latch" },
	{ label: "\\def", insert: "\\def{my_macro}[0]{\\enter}\n", kind: "cmd", desc: "Define compile-time text macro" },
	{ label: "\\use", insert: "\\use{sys}\n", kind: "cmd", desc: "Activate runtime namespace" },
	{ label: "\\call", insert: "\\call{sys.free}", kind: "cmd", desc: "Invoke runtime extension function" },
	{ label: "\\typenum", insert: "\\typenum{$x}", kind: "cmd", desc: "Render decimal integer as keystrokes" },
	{ label: "\\text", insert: "\\text{}payload\\endtext{}", kind: "cmd", desc: "Verbatim text keystroke block" },
	{ label: "\\endtext", insert: "\\endtext{}", kind: "cmd", desc: "End verbatim text block" },
	{ label: "\\rgb", insert: "\\rgb{255, 0, 0}", kind: "cmd", desc: "Pack 24-bit RGB scalar literal" },
	{ label: "\\pixelrect", insert: "\\pixelrect{target}{\\row{0xFF0000, 0x00FF00}}", kind: "cmd", desc: "Materialize snapshot pixel matrix" },
	{ label: "\\row", insert: "\\row{0xFF0000, 0x00FF00}", kind: "cmd", desc: "Pixel row color vector" },
	{ label: "\\kdown", insert: "\\kdown{ctrl}", kind: "cmd", desc: "Press and hold key down" },
	{ label: "\\kup", insert: "\\kup{ctrl}", kind: "cmd", desc: "Release key (empty {} for all keys)" },
	{ label: "\\enter", insert: "\\enter", kind: "key", desc: "Return / Enter key (alias: \\return)" },
	{ label: "\\return", insert: "\\return", kind: "key", desc: "Alias for \\enter (Usage 0x28)" },
	{ label: "\\esc", insert: "\\esc", kind: "key", desc: "Escape key (alias: \\escape)" },
	{ label: "\\escape", insert: "\\escape", kind: "key", desc: "Alias for \\esc (Usage 0x29)" },
	{ label: "\\tab", insert: "\\tab", kind: "key", desc: "Tab key (Usage 0x2B)" },
	{ label: "\\space", insert: "\\space", kind: "key", desc: "Spacebar character tap" },
	{ label: "\\bs", insert: "\\bs", kind: "key", desc: "Backspace key (alias: \\backspace)" },
	{ label: "\\backspace", insert: "\\backspace", kind: "key", desc: "Alias for \\bs (Usage 0x2A)" },
	{ label: "\\del", insert: "\\del", kind: "key", desc: "Forward Delete key (alias: \\delete)" },
	{ label: "\\delete", insert: "\\delete", kind: "key", desc: "Alias for \\del (Usage 0x4C)" },
	{ label: "\\ins", insert: "\\ins", kind: "key", desc: "Insert key (alias: \\insert)" },
	{ label: "\\insert", insert: "\\insert", kind: "key", desc: "Alias for \\ins (Usage 0x49)" },
	{ label: "\\home", insert: "\\home", kind: "key", desc: "Home navigation key" },
	{ label: "\\end", insert: "\\end", kind: "key", desc: "End navigation key" },
	{ label: "\\pgup", insert: "\\pgup", kind: "key", desc: "Page Up key (alias: \\pageup)" },
	{ label: "\\pageup", insert: "\\pageup", kind: "key", desc: "Alias for \\pgup (Usage 0x4B)" },
	{ label: "\\pgdn", insert: "\\pgdn", kind: "key", desc: "Page Down key (alias: \\pagedown)" },
	{ label: "\\pagedown", insert: "\\pagedown", kind: "key", desc: "Alias for \\pgdn (Usage 0x4E)" },
	{ label: "\\up", insert: "\\up", kind: "key", desc: "Arrow Up key" },
	{ label: "\\down", insert: "\\down", kind: "key", desc: "Arrow Down key" },
	{ label: "\\left", insert: "\\left", kind: "key", desc: "Arrow Left key" },
	{ label: "\\right", insert: "\\right", kind: "key", desc: "Arrow Right key" },
	{ label: "\\caps", insert: "\\caps", kind: "key", desc: "Caps Lock key (alias: \\capslock)" },
	{ label: "\\capslock", insert: "\\capslock", kind: "key", desc: "Alias for \\caps (Usage 0x39)" },
	{ label: "\\prtscr", insert: "\\prtscr", kind: "key", desc: "Print Screen key (Usage 0x46)" },
	{ label: "\\ctrl+c", insert: "\\ctrl+c", kind: "key", desc: "Copy clipboard shortcut" },
	{ label: "\\ctrl+v", insert: "\\ctrl+v", kind: "key", desc: "Paste clipboard shortcut" },
	{ label: "\\ctrl+z", insert: "\\ctrl+z", kind: "key", desc: "Undo shortcut" },
	{ label: "\\ctrl+a", insert: "\\ctrl+a", kind: "key", desc: "Select All shortcut" },
	{ label: "\\ctrl+s", insert: "\\ctrl+s", kind: "key", desc: "Save document shortcut" },
	{ label: "\\cmd+space", insert: "\\cmd+space", kind: "key", desc: "Spotlight / Launcher shortcut" },
	{ label: "\\alt+tab", insert: "\\alt+tab", kind: "key", desc: "Switch application window" },
	{ label: "\\volup", insert: "\\volup", kind: "key", desc: "Volume increment (Consumer 0x00E9)" },
	{ label: "\\voldn", insert: "\\voldn", kind: "key", desc: "Volume decrement (Consumer 0x00EA)" },
	{ label: "\\mute", insert: "\\mute", kind: "key", desc: "Audio mute toggle (Consumer 0x00E2)" },
	{ label: "\\media_playpause", insert: "\\media_playpause", kind: "key", desc: "Media Play/Pause (Consumer 0x00CD)" },
	{ label: "\\media_play", insert: "\\media_play", kind: "key", desc: "Media Play (Consumer 0x00B0)" },
	{ label: "\\media_pause", insert: "\\media_pause", kind: "key", desc: "Media Pause (Consumer 0x00B1)" },
	{ label: "\\media_next", insert: "\\media_next", kind: "key", desc: "Media Next track (Consumer 0x00B5)" },
	{ label: "\\media_prev", insert: "\\media_prev", kind: "key", desc: "Media Previous track (Consumer 0x00B6)" },
	{ label: "\\media_stop", insert: "\\media_stop", kind: "key", desc: "Media Stop track (Consumer 0x00B7)" },
	{ label: "\\brightup", insert: "\\brightup", kind: "key", desc: "Brightness increment (Consumer 0x006F)" },
	{ label: "\\brightdn", insert: "\\brightdn", kind: "key", desc: "Brightness decrement (Consumer 0x0070)" },
	{ label: "\\sys_sleep", insert: "\\sys_sleep", kind: "key", desc: "System Sleep / Suspend (Consumer 0x0032)" }
];

// 2. Syntax Directives per spec/dsl.md §3
export const SYNTAX_CANDIDATES: InspectorCandidate[] = [
	{ label: "\\#!syntax: free", insert: "\\#!syntax: free\n", kind: "syntax", desc: "Free-form layout (absorbs raw whitespace)" },
	{ label: "\\#!syntax: typewriter", insert: "\\#!syntax: typewriter\n", kind: "syntax", desc: "Typewriter layout (raw Enter/Space emit)" },
	{ label: "\\#!syntax: newline=ignore, space=emit", insert: "\\#!syntax: newline=ignore, space=emit\n", kind: "syntax", desc: "Absorb raw newlines, emit spaces" },
	{ label: "\\#!syntax: newline=emit, space=ignore", insert: "\\#!syntax: newline=emit, space=ignore\n", kind: "syntax", desc: "Emit newlines as Enter, absorb raw spaces" },
	{ label: "\\#!syntax: newline=ignore", insert: "\\#!syntax: newline=ignore\n", kind: "syntax", desc: "Layout config: ignore raw newlines" },
	{ label: "\\#!syntax: space=emit", insert: "\\#!syntax: space=emit\n", kind: "syntax", desc: "Layout config: emit raw space keystrokes" },
	{ label: "\\#!syntax: newline=emit", insert: "\\#!syntax: newline=emit\n", kind: "syntax", desc: "Layout config: emit raw Enter keystrokes" },
	{ label: "\\#!syntax: space=ignore", insert: "\\#!syntax: space=ignore\n", kind: "syntax", desc: "Layout config: ignore raw spaces and tabs" }
];

// 3. Special Escape Sequences per spec/dsl.md §2
export const ESCAPE_CANDIDATES: InspectorCandidate[] = [
	{ label: "\\#", insert: "\\# ", kind: "escape", desc: "Single-line comment (or literal hash)" },
	{ label: "\\\\", insert: "\\\\", kind: "escape", desc: "Literal backslash character" },
	{ label: "\\{", insert: "\\{", kind: "escape", desc: "Literal open brace character" },
	{ label: "\\}", insert: "\\}", kind: "escape", desc: "Literal close brace character" },
	{ label: "\\$", insert: "\\$", kind: "escape", desc: "Literal dollar character" }
];

// 4. Mouse buttons dictionary
export const MOUSE_BUTTON_CANDIDATES: InspectorCandidate[] = [
	{ label: "L", insert: "L", kind: "btn", desc: "Left mouse button (primary)" },
	{ label: "R", insert: "R", kind: "btn", desc: "Right mouse button (context menu)" },
	{ label: "M", insert: "M", kind: "btn", desc: "Middle mouse button (wheel click)" },
	{ label: "B", insert: "B", kind: "btn", desc: "Back mouse button (X1 / browser back)" },
	{ label: "F", insert: "F", kind: "btn", desc: "Forward mouse button (X2 / browser forward)" }
];

// 5. Key names dictionary inside holds and combinations
export const KEY_NAME_CANDIDATES: InspectorCandidate[] = [
	{ label: "ctrl", insert: "ctrl", kind: "key", desc: "Control modifier (aliases: control, lctrl, rctrl)" },
	{ label: "shift", insert: "shift", kind: "key", desc: "Shift modifier (alias: lshift)" },
	{ label: "alt", insert: "alt", kind: "key", desc: "Alt / Option modifier (aliases: opt, option, lalt)" },
	{ label: "gui", insert: "gui", kind: "key", desc: "GUI / Win / Cmd (aliases: win, cmd, command)" },
	{ label: "enter", insert: "enter", kind: "key", desc: "Return / Enter key (alias: return)" },
	{ label: "return", insert: "return", kind: "key", desc: "Alias for enter" },
	{ label: "esc", insert: "esc", kind: "key", desc: "Escape key (alias: escape)" },
	{ label: "escape", insert: "escape", kind: "key", desc: "Alias for esc" },
	{ label: "tab", insert: "tab", kind: "key", desc: "Tab key" },
	{ label: "space", insert: "space", kind: "key", desc: "Spacebar" },
	{ label: "bs", insert: "bs", kind: "key", desc: "Backspace key (alias: backspace)" },
	{ label: "del", insert: "del", kind: "key", desc: "Delete key (alias: delete)" },
	{ label: "home", insert: "home", kind: "key", desc: "Home key" },
	{ label: "end", insert: "end", kind: "key", desc: "End key" },
	{ label: "pgup", insert: "pgup", kind: "key", desc: "Page Up key (alias: pageup)" },
	{ label: "pgdn", insert: "pgdn", kind: "key", desc: "Page Down key (alias: pagedown)" },
	{ label: "up", insert: "up", kind: "key", desc: "Arrow Up key" },
	{ label: "down", insert: "down", kind: "key", desc: "Arrow Down key" },
	{ label: "left", insert: "left", kind: "key", desc: "Arrow Left key" },
	{ label: "right", insert: "right", kind: "key", desc: "Arrow Right key" },
	{ label: "f1", insert: "f1", kind: "key", desc: "Function key F1" },
	{ label: "f5", insert: "f5", kind: "key", desc: "Function key F5 (Refresh)" },
	{ label: "f12", insert: "f12", kind: "key", desc: "Function key F12 (DevTools)" }
];

// 6. Namespaces and extension calls
export const NAMESPACE_CANDIDATES: InspectorCandidate[] = [
	{ label: "sys", insert: "sys", kind: "ns", desc: "System control & heap diagnostic namespace" },
	{ label: "net", insert: "net", kind: "ns", desc: "Network Protocol v1 broadcast messaging" },
	{ label: "img", insert: "img", kind: "ns", desc: "Vision & pixel processing telemetry" }
];

export const EXT_FUNCTION_CANDIDATES: InspectorCandidate[] = [
	{ label: "sys.free", insert: "sys.free", kind: "cmd", desc: "Query device available heap RAM in bytes -> $ret" },
	{ label: "net.send", insert: "net.send}{sync}", kind: "cmd", desc: "Broadcast message tag to companion hosts" },
	{ label: "img.snap", insert: "img.snap}{0, 0, 100, 100}", kind: "cmd", desc: "Capture screen rectangle -> snapshot Handle in $ret" },
	{ label: "img.at", insert: "img.at}{$s}{0, 0}", kind: "cmd", desc: "Sample 24-bit RGB pixel color from snapshot" },
	{ label: "img.diff", insert: "img.diff}{$a}{$b}{16}", kind: "cmd", desc: "Compare snapshots and count differing pixels" },
	{ label: "img.watch", insert: "img.watch}{ready}{400, 200}{50, 10000}", kind: "cmd", desc: "Continuous telemetry stream pushing to $slot.img.<name>.val" },
	{ label: "img.unwatch", insert: "img.unwatch}{ready}", kind: "cmd", desc: "Cancel active telemetry stream subscription" }
];

// 7. Predicate Operators and Logic Blocks per spec/dsl.md §8
export const PREDICATE_OP_CANDIDATES: InspectorCandidate[] = [
	{ label: "=", insert: "=", kind: "op", desc: "Equal comparison operator" },
	{ label: "!=", insert: "!=", kind: "op", desc: "Not equal comparison operator" },
	{ label: "<", insert: "<", kind: "op", desc: "Less than comparison operator" },
	{ label: "<=", insert: "<=", kind: "op", desc: "Less than or equal operator" },
	{ label: ">", insert: ">", kind: "op", desc: "Greater than comparison operator" },
	{ label: ">=", insert: ">=", kind: "op", desc: "Greater than or equal operator" }
];

export const PREDICATE_LOGIC_CANDIDATES: InspectorCandidate[] = [
	{ label: "\\all", insert: "\\all{ {$x, >=, 0}, {$x, <=, 100} }", kind: "cmd", desc: "Conjunction AND logic block" },
	{ label: "\\any", insert: "\\any{ {$err, !=, 0}, {$timeout, =, 1} }", kind: "cmd", desc: "Disjunction OR logic block" }
];

export interface BracketCheckResult {
	balanced: boolean;
	depth: number;
	statusText: string;
	statusType: "balanced" | "warning" | "error";
	unclosedChar?: "{" | "[";
	unclosedLine?: number;
	unmatchedChar?: "}" | "]";
	unmatchedLine?: number;
	firstUnclosedLine?: number;
	firstUnclosedChar?: "{" | "[";
	firstUnclosedContext?: string;
	unclosedCount?: number;
}

export interface DetectionResult {
	context: string;
	rawToken: string;
	displayToken?: string;
	tokenStart: number;
	tokenEnd: number;
	matches: InspectorCandidate[];
}

export interface BraceFrame {
	openIndex: number;
	cmd: string;
	argIndex: number;
	isBracket: boolean;
	macroName?: string;
	repCount?: string;
	callTarget?: string;
	isVerbatimText?: boolean;
}

export const STRUCTURAL_COMMANDS = new Set([
	// Control flow & Logic
	"\\rep", "\\if", "\\guarded", "\\def", "\\all", "\\any",
	// State / Mutation
	"\\set", "\\add", "\\sub", "\\typenum",
	// Mouse
	"\\click", "\\move", "\\mdown", "\\mup",
	// Timing
	"\\delay", "\\pace",
	// Runtime
	"\\use", "\\call", "\\wait",
	// Graphics & Literals
	"\\pixelrect", "\\row", "\\rgb",
	// Key holds
	"\\kdown", "\\kup",
	// Verbatim
	"\\text", "\\endtext"
]);

export function getUserMacroNames(text: string): Set<string> {
	const set = new Set<string>();
	const regex = /\\def\s*\{([a-zA-Z][a-zA-Z0-9_]*)\}/g;
	let m: RegExpExecArray | null;
	while ((m = regex.exec(text)) !== null) {
		set.add(`\\${m[1]}`);
	}
	return set;
}

// Lone keys & consumer commands per spec/dsl.md §5.1 & §5.2.
// These never accept trailing brace/bracket groups; bare braces after them are literal text.
const LONE_KEY_NAMES = new Set([
	"enter", "return", "esc", "escape", "tab", "space", "bs", "backspace", "del", "delete",
	"home", "end", "pgup", "pageup", "pgdn", "pagedown", "up", "down", "left", "right",
	"f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
	"f13", "f14", "f15", "f16", "f17", "f18", "f19", "f20", "f21", "f22", "f23", "f24",
	"ctrl", "control", "lctrl", "rctrl", "shift", "lshift", "rshift", "alt", "opt", "option", "lalt", "ralt",
	"gui", "win", "cmd", "command", "rgui",
	"capslock", "numlock", "scrolllock", "printscreen", "pause", "menu", "application", "insert",
	"minus", "equals",
	"mute", "volup", "voldown", "media_next", "media_prev", "media_play", "media_pause", "media_stop"
]);

function isKeyCommand(name: string): boolean {
	const low = name.toLowerCase();
	// Single printable letter or digit: \a .. \z, \A .. \Z, \0 .. \9
	if (low.length === 1 && ((low >= "a" && low <= "z") || (low >= "0" && low <= "9"))) return true;
	// Chained key combinations: \ctrl+c, \a+b, etc.
	if (low.includes("+")) return true;
	return LONE_KEY_NAMES.has(low);
}

/**
 * Resolves the preceding command, argument index, and command start position
 * for an opening brace/bracket at openIndex.
 * Skips backwards across whitespace and preceding balanced {arg} / [mod] groups.
 */
function findPrecedingCommand(
	text: string,
	openIndex: number
): { cmd: string; argIndex: number; cmdStart: number; isKey: boolean } {
	let k = openIndex - 1;
	let argIndex = 0;

	while (k >= 0 && (text[k] === " " || text[k] === "\t" || text[k] === "\n" || text[k] === "\r")) k--;

	while (k >= 0 && (text[k] === "}" || text[k] === "]")) {
		const closeCh = text[k];
		const openCh = closeCh === "}" ? "{" : "[";
		let depth = 1;
		k--;
		while (k >= 0 && depth > 0) {
			if (text[k] === closeCh && text[k - 1] !== "\\") depth++;
			else if (text[k] === openCh && text[k - 1] !== "\\") depth--;
			k--;
		}
		if (depth === 0) {
			argIndex++;
			while (k >= 0 && (text[k] === " " || text[k] === "\t" || text[k] === "\n" || text[k] === "\r")) k--;
		} else {
			break;
		}
	}

	let cmd = "";
	let cmdStart = -1;
	let isKey = false;
	if (k >= 0) {
		const match = text.slice(0, k + 1).match(/\\([a-zA-Z][a-zA-Z0-9_]*)$/);
		if (match) {
			const cand = match[1];
			if (isKeyCommand(cand)) {
				isKey = true;
			} else {
				cmd = `\\${cand}`;
				cmdStart = match.index ?? -1;
			}
		}
	}

	return { cmd, argIndex, cmdStart, isKey };
}

interface ScanResult {
	cursorStack: BraceFrame[];
	depthAtCursor: number;
	stackAtEnd: BraceFrame[];
	firstUnmatchedClose: { char: "}" | "]"; line: number } | null;
	syntaxError: { msg: string; line: number } | null;
	unclosedVerbatim: { line: number; index: number } | null;
}

/**
 * Single-pass lexical & structural brace scanner.
 * Computes stack at cursor (for scope & depth) and stack at EOF (for unclosed/unmatched errors).
 */
function scanBraces(text: string, cursor?: number): ScanResult {
	const cur = cursor !== undefined ? cursor : text.length;
	const len = text.length;
	const stack: BraceFrame[] = [];
	let cursorStack: BraceFrame[] = [];
	let depthAtCursor = 0;
	let cursorCaptured = false;

	let currentLine = 1;
	let i = 0;
	let firstUnmatchedClose: { char: "}" | "]"; line: number } | null = null;
	let syntaxError: { msg: string; line: number } | null = null;
	let unclosedVerbatim: { line: number; index: number } | null = null;

	while (i < len) {
		if (i >= cur && !cursorCaptured) {
			cursorStack = [...stack];
			depthAtCursor = stack.filter(s => !s.isBracket).length;
			cursorCaptured = true;
		}

		const ch = text[i];

		// 1. Comments \# to end of line
		if (ch === "\\" && text[i + 1] === "#") {
			const nl = text.indexOf("\n", i);
			if (nl === -1) break;
			currentLine++;
			i = nl + 1;
			continue;
		}

		// 2. Line continuation \ + newline
		if (ch === "\\" && (text[i + 1] === "\n" || (text[i + 1] === "\r" && text[i + 2] === "\n"))) {
			if (text[i + 1] === "\r") i++;
			currentLine++;
			i += 2;
			continue;
		}

		// 3. Escapes: \\, \{, \}, \$
		if (ch === "\\" && (text[i + 1] === "\\" || text[i + 1] === "{" || text[i + 1] === "}" || text[i + 1] === "$")) {
			i += 2;
			continue;
		}

		// 4. Verbatim text block \text{}...\endtext{} (spec §5.6 mandates empty {})
		if (text.startsWith("\\text", i) && (i + 5 >= len || !/[a-zA-Z0-9_]/.test(text[i + 5]))) {
			if (text.slice(i + 5, i + 7) !== "{}") {
				syntaxError = { msg: `❌ \\text requires {} at line ${currentLine}`, line: currentLine };
				break;
			}
			const end = text.indexOf("\\endtext{}", i + 7);
			if (end === -1) {
				unclosedVerbatim = { line: currentLine, index: i };
				break;
			}
			for (let k = i; k < end + 10; k++) {
				if (text[k] === "\n") currentLine++;
			}
			i = end + 10;
			continue;
		}

		// 5. Mismatched \endtext outside text block (spec §5.6)
		if (text.startsWith("\\endtext", i) && (i + 8 >= len || !/[a-zA-Z0-9_]/.test(text[i + 8]))) {
			if (text.slice(i + 8, i + 10) !== "{}") {
				syntaxError = { msg: `❌ \\endtext requires {} at line ${currentLine}`, line: currentLine };
				break;
			}
			syntaxError = { msg: `❌ endtext without text block at line ${currentLine}`, line: currentLine };
			break;
		}

		if (ch === "\n") {
			currentLine++;
		} else if (ch === "{" || ch === "[") {
			const isBracket = (ch === "[");
			const { cmd, argIndex, cmdStart, isKey } = findPrecedingCommand(text, i);

			// Bracket [ without preceding command is literal
			if (isBracket && !cmd) {
				i++;
				continue;
			}
			// Opening brace directly following a lone key command:
			if (isKey) {
				if (text[i + 1] === "}") {
					// Empty {} separator per spec/dsl.md §2 (skip both { and })
					if (cur === i + 1 && !cursorCaptured) {
						cursorStack = [...stack];
						depthAtCursor = stack.filter(s => !s.isBracket).length;
						cursorCaptured = true;
					}
					i += 2;
					continue;
				}
				// Non-empty brace following a key is literal text
				i++;
				continue;
			}

			const parent = stack.length > 0 ? stack[stack.length - 1] : null;
			let context = cmd;
			let macroName = "";
			let repCount = "";
			let callTarget = "";

			if (cmdStart >= 0) {
				const cmdText = text.slice(cmdStart, i);
				if (cmd === "\\def") {
					const m = cmdText.match(/\\def\s*\{([a-zA-Z][a-zA-Z0-9_]*)\}/);
					if (m) macroName = m[1];
				} else if (cmd === "\\rep") {
					const m = cmdText.match(/\\rep\s*\{([^}]+)\}/);
					if (m) repCount = m[1].trim();
				} else if (cmd === "\\call") {
					const m = cmdText.match(/\\call\s*\{([a-zA-Z][a-zA-Z0-9_.]*)\}/);
					if (m) callTarget = m[1].trim();
				} else if (cmd === "\\pixelrect") {
					const m = cmdText.match(/\\pixelrect\s*\{([a-zA-Z][a-zA-Z0-9_]*)\}/);
					if (m) macroName = m[1];
				}
			}

			if (cmd === "\\if") {
				context = argIndex === 0 ? "\\if (Condition)" : (argIndex === 1 ? "\\if (Then-branch)" : "\\if (Else-branch)");
			} else if (cmd === "\\guarded") {
				const hasThrottle = cmdStart >= 0 && /\\guarded\s*\[/.test(text.slice(cmdStart, i));
				const eff = hasThrottle ? argIndex - 1 : argIndex;
				context = isBracket ? "\\guarded [throttle]" : (eff === 0 ? "\\guarded (Invariant)" : (eff === 1 ? "\\guarded (Body)" : "\\guarded (Abort-handler)"));
			} else if (cmd === "\\rep") {
				context = argIndex === 0 ? "\\rep (count)" : "\\rep (Body)";
			} else if (cmd === "\\def") {
				context = argIndex === 0 ? "\\def (name)" : (isBracket ? "\\def [argc]" : "\\def (Body)");
			} else if (cmd === "\\all") {
				context = "\\all (Conjunction)";
			} else if (cmd === "\\any") {
				context = "\\any (Disjunction)";
			} else if (cmd === "\\set" || cmd === "\\add" || cmd === "\\sub") {
				context = argIndex === 0 ? `${cmd} (var)` : `${cmd} (val)`;
			} else if (cmd === "\\click" || cmd === "\\mdown" || cmd === "\\mup") {
				context = `${cmd} (btn)`;
			} else if (cmd === "\\move") {
				context = "\\move > Arg Pack {dx, dy}";
			} else if (cmd === "\\call") {
				context = argIndex === 0 ? "\\call (target)" : "\\call > Arg Pack";
			} else if (cmd === "\\delay" || cmd === "\\pace") {
				context = `${cmd} (duration)`;
			} else if (cmd === "\\typenum") {
				context = "\\typenum (ref)";
			} else if (cmd === "\\wait") {
				context = isBracket ? "\\wait [timeout]" : "\\wait (slot)";
			} else if (cmd === "\\use") {
				context = "\\use (ns)";
			} else if (cmd === "\\kdown" || cmd === "\\kup") {
				context = `${cmd} (combo)`;
			} else if (cmd === "\\pixelrect") {
				context = argIndex === 0 ? "\\pixelrect (var)" : "\\pixelrect (rows)";
			} else if (cmd === "\\row") {
				context = "\\row > Pixel Pack";
			} else if (cmd === "\\rgb") {
				context = "\\rgb > Arg Pack {r, g, b}";
			} else if (!cmd) {
				const isInsideCondition = parent && (
					(parent.cmd === "\\if" && parent.argIndex === 0) ||
					(parent.cmd === "\\guarded" && parent.argIndex <= 1) ||
					parent.cmd === "\\all" ||
					parent.cmd === "\\any"
				);
				if (isInsideCondition) {
					context = "Predicate Triplet";
				} else if (parent && (parent.cmd === "\\call" || parent.cmd === "\\pixelrect" || parent.cmd === "\\move")) {
					context = "Arg Pack";
				} else {
					context = "Block";
				}
			}

			stack.push({
				char: ch,
				line: currentLine,
				openIndex: i,
				cmd,
				argIndex,
				context,
				isBracket,
				macroName,
				repCount,
				callTarget
			});
		} else if (ch === "}" || ch === "]") {
			const expected = ch === "}" ? "{" : "[";
			if (stack.length > 0) {
				const top = stack[stack.length - 1];
				if (top.char !== expected && !firstUnmatchedClose) {
					firstUnmatchedClose = { char: ch, line: currentLine };
				}
				stack.pop();
			} else if (!firstUnmatchedClose) {
				firstUnmatchedClose = { char: ch, line: currentLine };
			}
		}

		i++;
	}

	if (!cursorCaptured) {
		cursorStack = [...stack];
		depthAtCursor = stack.filter(s => !s.isBracket).length;
	}

	return {
		cursorStack,
		depthAtCursor,
		stackAtEnd: stack,
		firstUnmatchedClose,
		syntaxError,
		unclosedVerbatim
	};
}

/**
 * Checks whole-document bracket balance ({}, []) and returns active depth at cursor.
 * Exposes the root/first unclosed scope when multiple scopes are damaged.
 */
export function checkBracketBalance(text: string, cursor?: number): BracketCheckResult {
	const scan = scanBraces(text, cursor);

	if (scan.syntaxError) {
		return {
			balanced: false,
			depth: scan.depthAtCursor,
			statusText: scan.syntaxError.msg,
			statusType: "error",
			unmatchedLine: scan.syntaxError.line
		};
	}

	if (scan.firstUnmatchedClose) {
		return {
			balanced: false,
			depth: scan.depthAtCursor,
			statusText: `❌ Unmatched ${scan.firstUnmatchedClose.char} at line ${scan.firstUnmatchedClose.line}`,
			statusType: "error",
			unmatchedChar: scan.firstUnmatchedClose.char,
			unmatchedLine: scan.firstUnmatchedClose.line
		};
	}

	if (scan.unclosedVerbatim && (scan.stackAtEnd.length === 0 || scan.unclosedVerbatim.index < scan.stackAtEnd[0].openIndex)) {
		return {
			balanced: false,
			depth: scan.depthAtCursor,
			statusText: `⚠️ Unclosed \\text{} block at line ${scan.unclosedVerbatim.line} (missing \\endtext{})`,
			statusType: "warning",
			firstUnclosedLine: scan.unclosedVerbatim.line,
			firstUnclosedContext: "\\text{}",
			unclosedLine: scan.unclosedVerbatim.line,
			unclosedCount: scan.stackAtEnd.length + 1
		};
	}

	if (scan.stackAtEnd.length > 0) {
		const first = scan.stackAtEnd[0];
		const total = scan.stackAtEnd.length + (scan.unclosedVerbatim ? 1 : 0);
		const ctxDesc = first.context && first.context !== "scope" ? ` (${first.context})` : "";
		const statusText = total > 1
			? `⚠️ First unclosed ${first.char} at line ${first.line}${ctxDesc} (${total} unclosed in total)`
			: `⚠️ Unclosed ${first.char} at line ${first.line}${ctxDesc}`;

		return {
			balanced: false,
			depth: scan.depthAtCursor,
			statusText,
			statusType: "warning",
			firstUnclosedLine: first.line,
			firstUnclosedChar: first.char,
			firstUnclosedContext: first.context,
			unclosedLine: first.line,
			unclosedChar: first.char,
			unclosedCount: total
		};
	}

	return {
		balanced: true,
		depth: scan.depthAtCursor,
		statusText: `✓ Braces: Balanced (depth ${scan.depthAtCursor})`,
		statusType: "balanced"
	};
}

/**
 * Scans text up to cursor maintaining a lexical brace stack with command association.
 */
export function getBraceStack(text: string, cursor: number): BraceFrame[] {
	return scanBraces(text, cursor).cursorStack;
}

/**
 * Detects the structural scope breadcrumb at the current cursor position.
 * Deeply inspects predicate triplets, logic blocks, and argument packs.
 */
export function detectStructuralScope(text: string, cursor: number): string {
	const lineStart = text.lastIndexOf("\n", cursor - 1) + 1;
	const lineEnd = text.indexOf("\n", cursor);
	const lineText = text.slice(lineStart, lineEnd === -1 ? text.length : lineEnd);
	if (lineText.trimStart().startsWith("\\#!")) {
		return "Scope: Layout Preamble";
	}

	const lastTextOpen = text.lastIndexOf("\\text{}", cursor);
	if (lastTextOpen !== -1 && cursor >= lastTextOpen + 7) {
		const nextTextClose = text.indexOf("\\endtext{}", lastTextOpen + 7);
		if (nextTextClose === -1 || cursor < nextTextClose) {
			return "Scope: Verbatim Text (\\text)";
		}
	}

	const stack = getBraceStack(text, cursor);
	if (stack.length === 0) {
		if (lineText.trimStart().startsWith("\\#")) {
			return "Scope: Comment";
		}
		return "Scope: Document Root";
	}

	const segments = stack.map((frame, idx) => formatFrameScope(frame, idx === stack.length - 1));
	return segments.length > 0 ? `Scope: ${segments.join(" > ")}` : "Scope: Document Root";
}

/**
 * Formats a single BraceFrame into human-readable scope breadcrumb.
 * Unified for both leaf and ancestor frames to eliminate duplicate string formatting.
 */
function formatFrameScope(frame: BraceFrame, isLeaf: boolean): string {
	if (frame.cmd === "\\def") {
		if (frame.isBracket) return "\\def [argc]";
		if (frame.argIndex === 0) return "\\def (name)";
		const name = frame.macroName ? `{${frame.macroName}}` : (isLeaf ? "{name}" : "");
		return isLeaf ? `\\def${name} (Body)` : `\\def${name}`;
	}
	if (frame.cmd === "\\rep") {
		if (frame.argIndex === 0) return "\\rep (count)";
		const count = frame.repCount ? `{${frame.repCount}}` : (isLeaf ? "{count}" : "");
		return isLeaf ? `\\rep${count} (Body)` : `\\rep${count}`;
	}
	if (frame.cmd === "\\call") {
		if (frame.argIndex === 0) return "\\call (target)";
		const target = frame.callTarget ? `{${frame.callTarget}}` : "";
		if (!isLeaf) return `\\call${target}`;
		const sigs: Record<string, string> = {
			"img.snap": "Arg Pack {x, y, w, h}",
			"img.at": "Arg Pack {point}",
			"img.diff": "Arg Pack {a, b, tol}",
			"img.watch": "Arg Pack {name, x, y, interval, duration}",
			"net.send": "Arg Pack {msg}"
		};
		const sig = (frame.callTarget && sigs[frame.callTarget]) ? sigs[frame.callTarget] : "Arg Pack";
		return `\\call${target} > ${sig}`;
	}
	if (frame.cmd === "\\pixelrect" && !isLeaf && frame.argIndex >= 1) {
		const name = frame.macroName ? `{${frame.macroName}}` : "";
		return `\\pixelrect${name}`;
	}
	return frame.context || (frame.isBracket ? "Decorator" : "Block");
}

export function detectToken(text: string, cursor: number): DetectionResult | null {
	const lineStart = text.lastIndexOf("\n", cursor - 1) + 1;
	const lineBefore = text.slice(lineStart, cursor);

	// 1. Special Escapes: \\, \{, \}, \$
	const escMatch = lineBefore.match(/\\[\\{}$]$/);
	if (escMatch) {
		const rawToken = escMatch[0];
		const tokenStart = cursor - 2;
		const tokenEnd = cursor;
		const cand = ESCAPE_CANDIDATES.find(c => c.label === rawToken);
		return {
			context: "Escape Sequence",
			rawToken,
			tokenStart,
			tokenEnd,
			matches: cand ? [cand] : []
		};
	}

	// 2. Syntax Directives starting with \#! (e.g. \#!, \#!syn, \#!syntax:)
	const directiveMatch = lineBefore.match(/\\#!([^\r\n]*)$/);
	if (directiveMatch) {
		const rawToken = directiveMatch[0];
		const tokenStart = cursor - rawToken.length;
		const tokenEnd = cursor;
		const filterPrefix = rawToken.toLowerCase();

		const matches = SYNTAX_CANDIDATES.filter(c => {
			const lowerLabel = c.label.toLowerCase();
			if (lowerLabel.startsWith(filterPrefix)) return true;
			if (filterPrefix.includes(":")) {
				const queryVal = filterPrefix.split(":")[1].trim();
				const candVal = lowerLabel.split(":")[1]?.trim() || "";
				return candVal.startsWith(queryVal);
			}
			return false;
		});

		return {
			context: "Syntax Directive",
			rawToken,
			tokenStart,
			tokenEnd,
			matches
		};
	}

	// 3. Comment or Preamble Prefix \#
	if (lineBefore.endsWith("\\#")) {
		const rawToken = "\\#";
		const tokenStart = cursor - 2;
		const tokenEnd = cursor;

		const commentCandidate: InspectorCandidate = {
			label: "\\#",
			insert: "\\# ",
			kind: "escape",
			desc: "Single-line comment (or literal hash)"
		};

		// When cursor is right after \#, match comment and all syntax directives
		const matches = [commentCandidate, ...SYNTAX_CANDIDATES];

		return {
			context: "Comment / Directive",
			rawToken,
			tokenStart,
			tokenEnd,
			matches
		};
	}

	// 4. Variable / System Register / Slot ($ or ${)
	// (Note: literal \$ has already been captured by escape check above)
	const varMatch = lineBefore.match(/(?:\$|\$\{)([a-zA-Z0-9_.]*)$/);
	if (varMatch) {
		const isDelimited = varMatch[0].startsWith("${");
		const prefix = varMatch[1].toLowerCase();
		const tokenStart = cursor - varMatch[0].length;
		const tokenEnd = cursor;

		// Extract dynamic user variables declared in the script
		const declaredVars = Array.from(text.matchAll(/(?:\\set|\\pixelrect|\\add|\\sub)\{([a-zA-Z][a-zA-Z0-9_]*)\}/g))
			.map(m => m[1]);
		const referencedVars = Array.from(text.matchAll(/(?:\$|\$\{)([a-zA-Z][a-zA-Z0-9_]*)/g))
			.map(m => m[1])
			.filter(v => v !== "ret" && v !== "err" && v !== "timeout" && !v.startsWith("slot"));
		const uniqueUserVars = Array.from(new Set([...declaredVars, ...referencedVars]));

		const systemCandidates: InspectorCandidate[] = [
			{ label: "$ret", insert: isDelimited ? "${ret}" : "$ret", kind: "var", desc: "System return value / Handle ID of latest \\call" },
			{ label: "$err", insert: isDelimited ? "${err}" : "$err", kind: "var", desc: "System error code (0 = ERR_OK, 1..8 errors)" },
			{ label: "$timeout", insert: isDelimited ? "${timeout}" : "$timeout", kind: "var", desc: "Wait timeout flag (0 = arrived, 1 = timeout)" },
			{ label: "$slot.net.msg.val", insert: isDelimited ? "${slot.net.msg.val}" : "$slot.net.msg.val", kind: "slot", desc: "Network message arrival receptor slot" },
			{ label: "$slot.img.ready.val", insert: isDelimited ? "${slot.img.ready.val}" : "$slot.img.ready.val", kind: "slot", desc: "Vision stream arrival receptor slot" }
		];

		const userVarCandidates: InspectorCandidate[] = uniqueUserVars.map(v => ({
			label: `$${v}`,
			insert: isDelimited ? `\${${v}}` : `$${v}`,
			kind: "var",
			desc: `User variable '${v}' (general register)`
		}));

		const allVars = [...systemCandidates, ...userVarCandidates];
		const matches = allVars.filter(v => {
			if (!prefix) return true;
			const clean = v.label.replace(/^\$/, "").toLowerCase();
			return clean.startsWith(prefix) || clean.includes("." + prefix);
		});

		return {
			context: "Variable / Slot",
			rawToken: varMatch[0],
			tokenStart,
			tokenEnd,
			matches
		};
	}

	// 5. General Command / Key / Macro / Combo starting with \
	const cmdMatch = lineBefore.match(/\\([a-zA-Z0-9_+]*)$/);
	if (cmdMatch) {
		const rawToken = cmdMatch[0];
		const prefix = cmdMatch[1].toLowerCase();
		const tokenStart = cursor - rawToken.length;
		const tokenEnd = cursor;

		const userMacroMatches = Array.from(text.matchAll(/\\def\{([a-zA-Z][a-zA-Z0-9_]+)\}(?:\[(\d+)\])?/g));
		const userMacroCandidates: InspectorCandidate[] = userMacroMatches.map(m => ({
			label: `\\${m[1]}`,
			insert: `\\${m[1]}{}`,
			kind: "macro",
			desc: `User macro '${m[1]}' (${m[2] || "0"} arguments)`
		}));

		if (prefix === "") {
			// Alone '\' typed: candidate pool includes commands, top syntax presets, escapes, macros
			const allRoot = [
				...COMMAND_CANDIDATES,
				...SYNTAX_CANDIDATES.slice(0, 2),
				...ESCAPE_CANDIDATES,
				...userMacroCandidates
			];
			return {
				context: "Command / Key",
				rawToken: "\\",
				tokenStart,
				tokenEnd,
				matches: allRoot
			};
		}

		const allCmds = [...COMMAND_CANDIDATES, ...userMacroCandidates];
		const matches = allCmds.filter(c => {
			const clean = c.label.replace(/^\\/, "").toLowerCase();
			return clean.startsWith(prefix) || c.label.toLowerCase().startsWith(`\\${prefix}`);
		});

		return {
			context: "Command / Key",
			rawToken,
			tokenStart,
			tokenEnd,
			matches
		};
	}

	// 6. Context: Inside curly braces {...}
	const stack = getBraceStack(text, cursor);
	if (stack.length > 0) {
		const current = stack[stack.length - 1];
		const parent = stack.length >= 2 ? stack[stack.length - 2] : null;

		// 6a. Mouse click / holds: \click, \mdown, \mup
		if ((current.cmd === "\\click" || current.cmd === "\\mdown" || current.cmd === "\\mup") && current.argIndex === 0) {
			const content = text.slice(current.openIndex + 1, cursor);
			const commaIdx = content.indexOf(",");
			if (commaIdx === -1) {
				const leadingSpaces = content.length - content.trimStart().length;
				const argPrefix = content.trim().toLowerCase();
				const tokenStart = current.openIndex + 1 + leadingSpaces;
				const tokenEnd = cursor;
				const matches = MOUSE_BUTTON_CANDIDATES.filter(b => !argPrefix || b.label.toLowerCase().startsWith(argPrefix));
				return {
					context: "Mouse Button",
					rawToken: argPrefix || "{btn}",
					tokenStart,
					tokenEnd,
					matches
				};
			}
		}

		// 6b. Key holds: \kdown, \kup
		if (current.cmd === "\\kdown" || current.cmd === "\\kup") {
			const content = text.slice(current.openIndex + 1, cursor);
			const plusIdx = content.lastIndexOf("+");
			const segment = plusIdx !== -1 ? content.slice(plusIdx + 1) : content;
			const leadingSpaces = segment.length - segment.trimStart().length;
			const keyPrefix = segment.trim().toLowerCase();
			const tokenStart = (plusIdx !== -1 ? current.openIndex + 1 + plusIdx + 1 : current.openIndex + 1) + leadingSpaces;
			const tokenEnd = cursor;
			const matches = KEY_NAME_CANDIDATES.filter(k => !keyPrefix || k.label.toLowerCase().startsWith(keyPrefix));
			return {
				context: "Key / Modifier",
				rawToken: keyPrefix || "{key}",
				tokenStart,
				tokenEnd,
				matches
			};
		}

		// 6c. Namespace: \use
		if (current.cmd === "\\use" && current.argIndex === 0) {
			const content = text.slice(current.openIndex + 1, cursor);
			const leadingSpaces = content.length - content.trimStart().length;
			const argPrefix = content.trim().toLowerCase();
			const tokenStart = current.openIndex + 1 + leadingSpaces;
			const tokenEnd = cursor;
			const matches = NAMESPACE_CANDIDATES.filter(n => !argPrefix || n.label.toLowerCase().startsWith(argPrefix));
			return {
				context: "Namespace",
				rawToken: argPrefix || "{ns}",
				tokenStart,
				tokenEnd,
				matches
			};
		}

		// 6d. Extension call: \call
		if (current.cmd === "\\call" && current.argIndex === 0) {
			const content = text.slice(current.openIndex + 1, cursor);
			const leadingSpaces = content.length - content.trimStart().length;
			const argPrefix = content.trim().toLowerCase();
			const tokenStart = current.openIndex + 1 + leadingSpaces;
			const tokenEnd = cursor;
			const matches = EXT_FUNCTION_CANDIDATES.filter(f => !argPrefix || f.label.toLowerCase().startsWith(argPrefix));
			return {
				context: "Extension Call",
				rawToken: argPrefix || "{ns.fn}",
				tokenStart,
				tokenEnd,
				matches
			};
		}

		// 6e. Predicate / Comparison Operators in \if or \guarded
		const isIfOrGuarded = current.cmd === "\\if" || current.cmd === "\\guarded" ||
			(parent && (parent.cmd === "\\if" || parent.cmd === "\\guarded") && parent.argIndex === 0);

		if (isIfOrGuarded) {
			// Check if inside inner triplet e.g. \if{ {$x, 
			if (parent && (parent.cmd === "\\if" || parent.cmd === "\\guarded")) {
				const tripletContent = text.slice(current.openIndex + 1, cursor);
				const parts = tripletContent.split(",");
				if (parts.length === 2) {
					const opSegment = parts[1];
					const leadingSpaces = opSegment.length - opSegment.trimStart().length;
					const opPrefix = opSegment.trim().toLowerCase();
					const firstCommaPos = current.openIndex + 1 + parts[0].length;
					const tokenStart = firstCommaPos + 1 + leadingSpaces;
					const tokenEnd = cursor;
					const matches = PREDICATE_OP_CANDIDATES.filter(o => !opPrefix || o.label.startsWith(opPrefix));
					return {
						context: "Predicate Operator",
						rawToken: opPrefix || "{op}",
						tokenStart,
						tokenEnd,
						matches
					};
				}
			}

			// Directly inside condition block
			if (current.argIndex === 0) {
				const content = text.slice(current.openIndex + 1, cursor);
				const leadingSpaces = content.length - content.trimStart().length;
				const argPrefix = content.trim().toLowerCase();
				const tokenStart = current.openIndex + 1 + leadingSpaces;
				const tokenEnd = cursor;
				const combined = [...PREDICATE_LOGIC_CANDIDATES, ...PREDICATE_OP_CANDIDATES];
				const matches = combined.filter(c => !argPrefix || c.label.toLowerCase().startsWith(argPrefix) || c.label.replace(/^\\/, "").toLowerCase().startsWith(argPrefix));
				return {
					context: "Predicate Condition",
					rawToken: argPrefix || "{cond}",
					tokenStart,
					tokenEnd,
					matches
				};
			}
		}
	}

	return null;
}
