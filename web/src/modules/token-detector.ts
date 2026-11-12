import type {
	CandidateKind,
	InspectorCandidate,
	BlockRule,
	MultiBlockSignature
} from "./catalog.ts";
import {
	COMMAND_CANDIDATES,
	SYNTAX_CANDIDATES,
	ESCAPE_CANDIDATES,
	MOUSE_BUTTON_CANDIDATES,
	KEY_NAME_CANDIDATES,
	NAMESPACE_CANDIDATES,
	EXT_FUNCTION_CANDIDATES,
	PREDICATE_LOGIC_CANDIDATES,
	PREDICATE_OP_CANDIDATES,
	MULTI_BLOCK_SIGNATURES,
	LONE_KEY_NAMES
} from "./catalog.ts";

export type { CandidateKind, InspectorCandidate, BlockRule, MultiBlockSignature };
export {
	COMMAND_CANDIDATES,
	SYNTAX_CANDIDATES,
	ESCAPE_CANDIDATES,
	MOUSE_BUTTON_CANDIDATES,
	KEY_NAME_CANDIDATES,
	NAMESPACE_CANDIDATES,
	EXT_FUNCTION_CANDIDATES,
	PREDICATE_LOGIC_CANDIDATES,
	PREDICATE_OP_CANDIDATES,
	MULTI_BLOCK_SIGNATURES
};

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
	char: "{" | "[";
	line: number;
	openIndex: number;
	cmd: string;
	cmdStart: number;
	argIndex: number;
	context: string;
	isBracket: boolean;
	macroName?: string;
	repCount?: string;
	callTarget?: string;
	isVerbatimText?: boolean;
	hasThrottle?: boolean;
}

export function getNextRequiredBlock(
	cmd: string,
	argIndex: number,
	isBracket: boolean,
	hasThrottle?: boolean
): BlockRule | null {
	const sig = MULTI_BLOCK_SIGNATURES[cmd];
	if (!sig) return null;

	if (cmd === "\\def") {
		// arg 0 is {name}. Whether next is [argc] or {body}, the mandatory block is {body}.
		// If isBracket is true ([argc]), next mandatory block is {body}.
		// If argIndex >= 1 and not bracket, {body} is completed.
		if (isBracket || argIndex === 0) {
			return { name: "body", kind: "brace", required: true };
		}
		return null;
	}

	if (cmd === "\\guarded") {
		if (isBracket) {
			return { name: "predicate", kind: "brace", required: true };
		}
		const eff = hasThrottle ? argIndex - 1 : argIndex;
		if (eff === 0) {
			return { name: "body", kind: "brace", required: true };
		}
		return null;
	}

	// For \if, \rep, \set, \add, \sub, \pixelrect:
	// block 0 closes -> block 1 is required
	if (argIndex === 0 && sig.rules.length > 1 && sig.rules[1].required) {
		return sig.rules[1];
	}

	return null;
}

export function skipWhitespaceAndComments(text: string, fromIndex: number): number {
	let idx = fromIndex;
	const len = text.length;
	while (idx < len) {
		const c = text[idx];
		if (c === " " || c === "\t" || c === "\r" || c === "\n") {
			idx++;
			continue;
		}
		// Comment \# to newline
		if (c === "\\" && text[idx + 1] === "#") {
			const nl = text.indexOf("\n", idx);
			if (nl === -1) return len;
			idx = nl + 1;
			continue;
		}
		// Line continuation \ + newline
		if (c === "\\" && (text[idx + 1] === "\n" || (text[idx + 1] === "\r" && text[idx + 2] === "\n"))) {
			idx += (text[idx + 1] === "\r" ? 3 : 2);
			continue;
		}
		break;
	}
	return idx;
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

export interface TransitionScope {
	cmd: string;
	awaiting: string;
	gapStart: number;
	gapEnd: number;
	line: number;
}

export interface IncompleteStatement {
	cmd: string;
	missing: string;
	line: number;
}

interface ScanResult {
	cursorStack: BraceFrame[];
	depthAtCursor: number;
	stackAtEnd: BraceFrame[];
	firstUnmatchedClose: { char: "}" | "]"; line: number } | null;
	syntaxError: { msg: string; line: number } | null;
	unclosedVerbatim: { line: number; index: number } | null;
	cursorTransition: TransitionScope | null;
	firstIncompleteStatement: IncompleteStatement | null;
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
	let cursorTransition: TransitionScope | null = null;
	let firstIncompleteStatement: IncompleteStatement | null = null;

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

			const hasThrottle = (cmd === "\\guarded" && cmdStart >= 0 && /\\guarded\s*\[/.test(text.slice(cmdStart, i)));

			if (cmd === "\\if") {
				context = argIndex === 0 ? "\\if (Condition)" : (argIndex === 1 ? "\\if (Then-branch)" : "\\if (Else-branch)");
			} else if (cmd === "\\guarded") {
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
				cmdStart,
				argIndex,
				context,
				isBracket,
				macroName,
				repCount,
				callTarget,
				hasThrottle
			});
		} else if (ch === "}" || ch === "]") {
			const expected = ch === "}" ? "{" : "[";
			if (stack.length > 0) {
				const top = stack[stack.length - 1];
				if (top.char !== expected && !firstUnmatchedClose) {
					firstUnmatchedClose = { char: ch, line: currentLine };
				}
				stack.pop();

				// Statement completeness & transition scope detection
				const req = getNextRequiredBlock(top.cmd, top.argIndex, top.isBracket, top.hasThrottle);
				if (req) {
					const gapStart = i + 1;
					const nextNonWs = skipWhitespaceAndComments(text, gapStart);
					const nextCh = nextNonWs < len ? text[nextNonWs] : null;
					const isPresent = nextCh === "{" || (top.cmd === "\\def" && nextCh === "[");

					if (!isPresent && !firstIncompleteStatement) {
						firstIncompleteStatement = {
							cmd: top.cmd,
							missing: req.name,
							line: top.line
						};
					}

					if (cur >= gapStart && cur <= nextNonWs && !cursorTransition) {
						cursorTransition = {
							cmd: top.cmd,
							awaiting: req.name,
							gapStart,
							gapEnd: nextNonWs,
							line: top.line
						};
					}
				}
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
		unclosedVerbatim,
		cursorTransition,
		firstIncompleteStatement
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

	const inc = scan.firstIncompleteStatement;
	const unclosed = scan.stackAtEnd.length > 0 ? scan.stackAtEnd[0] : null;

	if (inc && (!unclosed || inc.line <= unclosed.line)) {
		return {
			balanced: false,
			depth: scan.depthAtCursor,
			statusText: `⚠️ Incomplete statement: ${inc.cmd} requires {${inc.missing}}`,
			statusType: "warning",
			unclosedLine: inc.line,
			firstUnclosedLine: inc.line,
			firstUnclosedContext: inc.cmd
		};
	}

	if (unclosed) {
		const total = scan.stackAtEnd.length + (scan.unclosedVerbatim ? 1 : 0);
		const ctxDesc = unclosed.context && unclosed.context !== "scope" ? ` (${unclosed.context})` : "";
		const statusText = total > 1
			? `⚠️ First unclosed ${unclosed.char} at line ${unclosed.line}${ctxDesc} (${total} unclosed in total)`
			: `⚠️ Unclosed ${unclosed.char} at line ${unclosed.line}${ctxDesc}`;

		return {
			balanced: false,
			depth: scan.depthAtCursor,
			statusText,
			statusType: "warning",
			firstUnclosedLine: unclosed.line,
			firstUnclosedChar: unclosed.char,
			firstUnclosedContext: unclosed.context,
			unclosedLine: unclosed.line,
			unclosedChar: unclosed.char,
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
 * Determines whether the cursor is located inside a verbatim text block (\text{}...\endtext{}).
 * Per spec/dsl.md §5.6, payload characters emit literal keystrokes and all commands, comments,
 * escapes, and variable sigils are treated as verbatim payload text.
 */
export function isInsideVerbatim(text: string, cursor: number): boolean {
	const lastTextOpen = text.lastIndexOf("\\text{}", cursor);
	if (lastTextOpen === -1 || cursor < lastTextOpen + 7) {
		return false;
	}
	const nextTextClose = text.indexOf("\\endtext{}", lastTextOpen + 7);
	if (nextTextClose === -1) {
		return true;
	}
	return cursor < nextTextClose;
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

	if (isInsideVerbatim(text, cursor)) {
		return "Scope: Verbatim Text (\\text)";
	}

	const scan = scanBraces(text, cursor);
	const stack = scan.cursorStack;

	if (lineText.trimStart().startsWith("\\#")) {
		return "Scope: Comment";
	}

	if (scan.cursorTransition) {
		const transitionSegment = `${scan.cursorTransition.cmd} (awaiting {${scan.cursorTransition.awaiting}})`;
		const segments = stack.map((frame, idx) => formatFrameScope(frame, idx === stack.length - 1));
		return segments.length > 0
			? `Scope: ${[...segments, transitionSegment].join(" > ")}`
			: `Scope: ${transitionSegment}`;
	}

	if (stack.length === 0) {
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

function getAllVariableCandidates(text: string, isDelimited = false): InspectorCandidate[] {
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

	return [...systemCandidates, ...userVarCandidates];
}

export function detectToken(text: string, cursor: number): DetectionResult | null {
	const lineStart = text.lastIndexOf("\n", cursor - 1) + 1;
	const lineBefore = text.slice(lineStart, cursor);

	// 0. Inside verbatim text blocks (\text{}...\endtext{}), all escapes, variables,
	// comments, and regular commands are suppressed per spec/dsl.md §5.6.
	// The ONLY valid syntax construct is the block terminator \endtext{}.
	if (isInsideVerbatim(text, cursor)) {
		const endtextMatch = lineBefore.match(/(?:^|[^\\])(\\[a-zA-Z0-9_]*)$/);
		if (endtextMatch) {
			const rawToken = endtextMatch[1];
			const tokenStart = cursor - rawToken.length;
			const tokenEnd = cursor;
			const cand = COMMAND_CANDIDATES.find(c => c.label === "\\endtext");
			const matches = (cand && "\\endtext".startsWith(rawToken)) ? [cand] : [];
			return {
				context: "Verbatim Block Terminator",
				rawToken,
				tokenStart,
				tokenEnd,
				matches
			};
		}
		return null;
	}

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

		const allVars = getAllVariableCandidates(text, isDelimited);
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

	// 5. Context: Inside curly braces {...}
	const stack = getBraceStack(text, cursor);
	if (stack.length > 0) {
		const current = stack[stack.length - 1];
		const parent = stack.length >= 2 ? stack[stack.length - 2] : null;

		// 5a. Mouse click / holds: \click, \mdown, \mup
		if ((current.cmd === "\\click" || current.cmd === "\\mdown" || current.cmd === "\\mup") && current.argIndex === 0) {
			const content = text.slice(current.openIndex + 1, cursor);
			const commaIdx = content.indexOf(",");
			if (commaIdx === -1) {
				const leadingSpaces = content.length - content.trimStart().length;
				const argPrefix = content.trim().toLowerCase().replace(/^\\/, "");
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
			// After comma: click count
			const countSegment = content.slice(commaIdx + 1);
			const leadingSpaces = countSegment.length - countSegment.trimStart().length;
			const countPrefix = countSegment.trim();
			return {
				context: "Click Count",
				rawToken: countPrefix || "{count}",
				tokenStart: current.openIndex + 1 + commaIdx + 1 + leadingSpaces,
				tokenEnd: cursor,
				matches: []
			};
		}

		// 5b. Key holds: \kdown, \kup
		if (current.cmd === "\\kdown" || current.cmd === "\\kup") {
			const content = text.slice(current.openIndex + 1, cursor);
			const plusIdx = content.lastIndexOf("+");
			const segment = plusIdx !== -1 ? content.slice(plusIdx + 1) : content;
			const leadingSpaces = segment.length - segment.trimStart().length;
			const keyPrefix = segment.trim().toLowerCase().replace(/^\\/, "");
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

		// 5c. Namespace: \use
		if (current.cmd === "\\use" && current.argIndex === 0) {
			const content = text.slice(current.openIndex + 1, cursor);
			const leadingSpaces = content.length - content.trimStart().length;
			const argPrefix = content.trim().toLowerCase().replace(/^\\/, "");
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

		// 5d. Extension call: \call
		if (current.cmd === "\\call" && current.argIndex === 0) {
			const content = text.slice(current.openIndex + 1, cursor);
			const leadingSpaces = content.length - content.trimStart().length;
			const argPrefix = content.trim().toLowerCase().replace(/^\\/, "");
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

		// 5e. Predicate Triplet: {lhs, op, rhs} in \if, \guarded, \all, \any
		const isTriplet = current.context === "Predicate Triplet" || (
			parent && (
				(parent.cmd === "\\if" && parent.argIndex === 0) ||
				(parent.cmd === "\\guarded" && (parent.argIndex === 0 || parent.context?.includes("Invariant"))) ||
				parent.cmd === "\\all" ||
				parent.cmd === "\\any"
			) && (!current.cmd || current.cmd === "")
		);

		if (isTriplet) {
			const tripletContent = text.slice(current.openIndex + 1, cursor);
			const parts = tripletContent.split(",");

			if (parts.length === 1) {
				// LHS Position
				const lhsSegment = parts[0];
				const leadingSpaces = lhsSegment.length - lhsSegment.trimStart().length;
				const lhsPrefix = lhsSegment.trim().toLowerCase();
				const tokenStart = current.openIndex + 1 + leadingSpaces;
				const tokenEnd = cursor;
				const allVars = getAllVariableCandidates(text, false);
				const matches = allVars.filter(v => {
					if (!lhsPrefix) return true;
					const clean = v.label.replace(/^\$/, "").toLowerCase();
					return clean.startsWith(lhsPrefix) || v.label.toLowerCase().startsWith(lhsPrefix);
				});
				return {
					context: "Predicate Operand",
					rawToken: lhsSegment.trim() || "{lhs}",
					tokenStart,
					tokenEnd,
					matches
				};
			}

			if (parts.length === 2) {
				// Operator Position
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

			if (parts.length === 3) {
				// RHS Position
				const rhsSegment = parts[2];
				const leadingSpaces = rhsSegment.length - rhsSegment.trimStart().length;
				const rhsPrefix = rhsSegment.trim().toLowerCase();
				const secondCommaPos = current.openIndex + 1 + parts[0].length + 1 + parts[1].length;
				const tokenStart = secondCommaPos + 1 + leadingSpaces;
				const tokenEnd = cursor;
				const allVars = getAllVariableCandidates(text, false);
				const matches = allVars.filter(v => {
					if (!rhsPrefix) return true;
					const clean = v.label.replace(/^\$/, "").toLowerCase();
					return clean.startsWith(rhsPrefix) || v.label.toLowerCase().startsWith(rhsPrefix);
				});
				return {
					context: "Predicate Operand",
					rawToken: rhsSegment.trim() || "{rhs}",
					tokenStart,
					tokenEnd,
					matches
				};
			}
		}

		// 5f. Condition block directly under \if, \guarded, \all, \any
		const isDirectCondition = (current.cmd === "\\if" && current.argIndex === 0) ||
			(current.cmd === "\\guarded" && (current.argIndex === 0 || current.context?.includes("Invariant"))) ||
			current.cmd === "\\all" || current.cmd === "\\any";

		if (isDirectCondition) {
			const cmdMatch = lineBefore.match(/\\([a-zA-Z0-9_]*)$/);
			if (cmdMatch) {
				const rawToken = cmdMatch[0];
				const prefix = cmdMatch[1].toLowerCase();
				const tokenStart = cursor - rawToken.length;
				const tokenEnd = cursor;
				const matches = PREDICATE_LOGIC_CANDIDATES.filter(c =>
					!prefix || c.label.toLowerCase().startsWith(rawToken.toLowerCase()) || c.label.replace(/^\\/, "").toLowerCase().startsWith(prefix)
				);
				return {
					context: "Predicate Condition",
					rawToken,
					tokenStart,
					tokenEnd,
					matches
				};
			}

			const content = text.slice(current.openIndex + 1, cursor);
			const leadingSpaces = content.length - content.trimStart().length;
			const argPrefix = content.trim().toLowerCase();
			const tokenStart = current.openIndex + 1 + leadingSpaces;
			const tokenEnd = cursor;

			const tripletTemplate: InspectorCandidate = {
				label: "{}",
				insert: "{ $ret, =, 0 }",
				kind: "op",
				desc: "Comparison triplet {lhs, op, rhs}"
			};
			const isLogicCmd = current.cmd === "\\all" || current.cmd === "\\any";
			const pool = isLogicCmd ? [tripletTemplate] : [...PREDICATE_LOGIC_CANDIDATES, tripletTemplate];
			const matches = pool.filter(c => !argPrefix || c.label.toLowerCase().startsWith(argPrefix) || c.label.replace(/^\\/, "").toLowerCase().startsWith(argPrefix));
			return {
				context: "Predicate Condition",
				rawToken: argPrefix || "{cond}",
				tokenStart,
				tokenEnd,
				matches
			};
		}

		// 5g. Argument Packs: \move, \rgb, \call
		if (current.cmd === "\\move") {
			const content = text.slice(current.openIndex + 1, cursor);
			const parts = content.split(",");
			const isDx = parts.length === 1;
			return {
				context: isDx ? "Move X (dx)" : "Move Y (dy)",
				rawToken: (isDx ? parts[0] : parts[1]).trim() || (isDx ? "{dx}" : "{dy}"),
				tokenStart: current.openIndex + 1,
				tokenEnd: cursor,
				matches: []
			};
		}

		if (current.cmd === "\\rgb") {
			const content = text.slice(current.openIndex + 1, cursor);
			const parts = content.split(",");
			const idx = parts.length;
			const label = idx === 1 ? "RGB Red" : (idx === 2 ? "RGB Green" : "RGB Blue");
			return {
				context: label,
				rawToken: parts[idx - 1].trim() || (idx === 1 ? "{r}" : (idx === 2 ? "{g}" : "{b}")),
				tokenStart: current.openIndex + 1,
				tokenEnd: cursor,
				matches: []
			};
		}

		if (current.cmd === "\\call" && current.argIndex >= 1) {
			const content = text.slice(current.openIndex + 1, cursor);
			const target = current.callTarget || "extension";
			return {
				context: `Call Args (${target})`,
				rawToken: content.trim() || "{args}",
				tokenStart: current.openIndex + 1,
				tokenEnd: cursor,
				matches: []
			};
		}
	}

	// 6. General Command / Key / Macro / Combo starting with \ (Valid in CodeBlocks and Document Root)
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

	// 7. Transition Scope Completion: awaiting subsequent mandatory block
	const scan = scanBraces(text, cursor);
	if (scan.cursorTransition) {
		const { cmd, awaiting } = scan.cursorTransition;
		const templateInsert = (awaiting === "value" || awaiting === "var") ? "{}" : "{\n  \n}";
		return {
			context: `Awaiting {${awaiting}}`,
			rawToken: `{${awaiting}}`,
			tokenStart: cursor,
			tokenEnd: cursor,
			matches: [
				{
					label: "{}",
					insert: templateInsert,
					kind: "syntax",
					desc: `Code block {${awaiting}} for ${cmd}`
				}
			]
		};
	}

	return null;
}
