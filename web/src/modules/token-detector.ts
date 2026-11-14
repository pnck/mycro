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
	CALL_ARG_COUNTS,
	LONE_KEY_NAMES
} from "./catalog.ts";


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

interface PendingTarget {
	cmd: string;
	stage: string;
	cmdStart: number;
	argIndex: number;
	hasThrottle?: boolean;
	macroName?: string;
	repCount?: string;
	callTarget?: string;
	expectedArgs?: number;
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
	userMacros: Set<string>;
	userVars: Set<string>;
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
		if (c === "\\" && text[idx + 1] === "#") {
			const nl = text.indexOf("\n", idx);
			if (nl === -1) return len;
			idx = nl + 1;
			continue;
		}
		if (c === "\\" && (text[idx + 1] === "\n" || (text[idx + 1] === "\r" && text[idx + 2] === "\n"))) {
			idx += (text[idx + 1] === "\r" ? 3 : 2);
			continue;
		}
		break;
	}
	return idx;
}

export const STRUCTURAL_COMMANDS = new Set(
	"\\rep \\if \\guarded \\def \\all \\any \\set \\add \\sub \\typenum \\click \\move \\mdown \\mup \\delay \\pace \\use \\call \\wait \\pixelrect \\row \\rgb \\kdown \\kup \\tap \\text \\endtext".split(" ")
);

function isKeyCommand(name: string): boolean {
	const low = name.toLowerCase();
	if (low.length === 1 && ((low >= "a" && low <= "z") || (low >= "0" && low <= "9"))) return true;
	if (low.includes("+")) return true;
	return LONE_KEY_NAMES.has(low);
}

function isCondScope(f: BraceFrame | null): boolean {
	return !f ? false : (
		(f.cmd === "\\if" && f.argIndex === 0) ||
		(f.cmd === "\\guarded" && (f.argIndex === 0 || Boolean(f.context?.includes("Invariant")) || (f.hasThrottle ? f.argIndex === 1 : f.argIndex === 0))) ||
		f.cmd === "\\all" || f.cmd === "\\any"
	);
}

/**
 * Affine Lexical DFA state machine.
 * Streamingly analyzes lexical syntax, tracks layout configuration (\#!syntax:),
 * and maintains structural brace frames and symbol tables in a single pass.
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

	const userMacros = new Set<string>();
	const userMacroArgc: Record<string, number> = {};
	const userVars = new Set<string>();

	let emitNewline = true;
	let emitSpace = true;
	let layoutFrozen = false;
	let pendingTarget: PendingTarget | null = null;

	while (i < len) {
		if (i >= cur && !cursorCaptured) {
			cursorStack = [...stack];
			depthAtCursor = stack.filter(s => !s.isBracket).length;
			cursorCaptured = true;
		}

		const ch = text[i];

		// 1. Line continuation: \ + newline
		if (ch === "\\" && (text[i + 1] === "\n" || (text[i + 1] === "\r" && text[i + 2] === "\n"))) {
			if (text[i + 1] === "\r") i++;
			currentLine++;
			i += 2;
			continue;
		}

		// 2. Comments and directives: \#
		if (ch === "\\" && text[i + 1] === "#") {
			if (!layoutFrozen && text[i + 2] === "!") {
				const nl = text.indexOf("\n", i);
				const lineEnd = nl === -1 ? len : nl;
				const dirLine = text.slice(i + 3, lineEnd).trim();
				if (dirLine.startsWith("syntax:")) {
					const cfg = dirLine.slice(7).trim();
					if (cfg === "free") {
						emitNewline = false;
						emitSpace = false;
					} else if (cfg === "typewriter") {
						emitNewline = true;
						emitSpace = true;
					} else {
						const parts = cfg.split(",");
						for (const p of parts) {
							const [k, v] = p.split("=").map(s => s.trim());
							if (k === "newline") emitNewline = (v === "emit");
							if (k === "space") emitSpace = (v === "emit");
						}
					}
				}
				currentLine++;
				i = nl === -1 ? len : nl + 1;
				continue;
			}
			const nl = text.indexOf("\n", i);
			if (nl === -1) break;
			currentLine++;
			i = nl + 1;
			continue;
		}

		// 3. Escapes: \\, \{, \}, \$
		if (ch === "\\" && (text[i + 1] === "\\" || text[i + 1] === "{" || text[i + 1] === "}" || text[i + 1] === "$")) {
			layoutFrozen = true;
			i += 2;
			continue;
		}

		// 4. Verbatim text block \text{}...\endtext{}
		if (text.startsWith("\\text", i) && (i + 5 >= len || !/[a-zA-Z0-9_]/.test(text[i + 5]))) {
			layoutFrozen = true;
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
			pendingTarget = null;
			continue;
		}

		// 5. Mismatched \endtext outside text block
		if (text.startsWith("\\endtext", i) && (i + 8 >= len || !/[a-zA-Z0-9_]/.test(text[i + 8]))) {
			layoutFrozen = true;
			if (text.slice(i + 8, i + 10) !== "{}") {
				syntaxError = { msg: `❌ \\endtext requires {} at line ${currentLine}`, line: currentLine };
				break;
			}
			syntaxError = { msg: `❌ endtext without text block at line ${currentLine}`, line: currentLine };
			break;
		}

		// 6. Whitespace and newlines
		if (ch === "\n") {
			currentLine++;
			i++;
			continue;
		}
		if (ch === " " || ch === "\t" || ch === "\r") {
			i++;
			continue;
		}

		// First non-whitespace, non-comment token freezes layout
		layoutFrozen = true;

		// 7. Variable reference symbol $
		if (ch === "$" && (i === 0 || text[i - 1] !== "\\")) {
			const vm = text.slice(i).match(/^\$?\{?([a-zA-Z][a-zA-Z0-9_]*)/);
			if (vm) {
				const vname = vm[1];
				if (vname !== "ret" && vname !== "err" && vname !== "timeout" && !vname.startsWith("slot")) {
					userVars.add(vname);
				}
			}
		}

		// 8. Backslash command
		if (ch === "\\") {
			const cmdMatch = text.slice(i).match(/^\\([a-zA-Z][a-zA-Z0-9_]*)/);
			if (cmdMatch) {
				const rawCmd = cmdMatch[0];
				const name = cmdMatch[1].toLowerCase();
				const cmdPos = i;
				i += rawCmd.length;

				if (isKeyCommand(name)) {
					// Lone key commands can be followed by optional empty {}
					if (text[i] === "{" && text[i + 1] === "}") {
						if (cur === i + 1 && !cursorCaptured) {
							cursorStack = [...stack];
							depthAtCursor = stack.filter(s => !s.isBracket).length;
							cursorCaptured = true;
						}
						i += 2;
					} else if (text[i] === "{" && name.length === 1 && ((name >= "a" && name <= "z") || (name >= "0" && name <= "9"))) {
						// Single printable key followed immediately by literal brace (e.g. \a{)
						i++;
					}
					pendingTarget = null;
					continue;
				}

				const stages: Record<string, string> = {
					def: "name", wait: "timeout_or_slot", guarded: "throttle_or_pred",
					if: "cond", rep: "count", set: "var", add: "var", sub: "var",
					pixelrect: "name", call: "target"
				};
				const isUserMacro = userMacros.has(name);
				const macroArgc = isUserMacro ? (userMacroArgc[name] || 0) : 0;
				if (isUserMacro && macroArgc > 0) {
					pendingTarget = {
						cmd: `\\${name}`,
						stage: "args",
						cmdStart: cmdPos,
						argIndex: 0,
						expectedArgs: macroArgc
					};
				} else if (!stages[name] && !STRUCTURAL_COMMANDS.has(`\\${name}`) && text[i] === "{" && text[i + 1] === "}") {
					i += 2;
					pendingTarget = null;
				} else {
					pendingTarget = {
						cmd: `\\${name}`,
						stage: stages[name] || "arg",
						cmdStart: cmdPos,
						argIndex: 0,
						hasThrottle: false
					};
				}
				continue;
			}
		}

		// 9. Square bracket open [
		if (ch === "[") {
			if (pendingTarget && (
				(pendingTarget.cmd === "\\def" && pendingTarget.stage === "argc_or_body") ||
				(pendingTarget.cmd === "\\wait" && pendingTarget.stage === "timeout_or_slot") ||
				(pendingTarget.cmd === "\\guarded" && pendingTarget.stage === "throttle_or_pred")
			)) {
				const context = pendingTarget.cmd === "\\def" ? "\\def [argc]" :
					(pendingTarget.cmd === "\\wait" ? "\\wait [timeout]" : "\\guarded [throttle]");
				const hasThrottle = (pendingTarget.cmd === "\\guarded");
				stack.push({
					char: "[",
					line: currentLine,
					openIndex: i,
					cmd: pendingTarget.cmd,
					cmdStart: pendingTarget.cmdStart,
					argIndex: pendingTarget.argIndex,
					context,
					isBracket: true,
					macroName: pendingTarget.macroName,
					repCount: pendingTarget.repCount,
					callTarget: pendingTarget.callTarget,
					hasThrottle
				});
				pendingTarget = null;
				i++;
				continue;
			}
			i++;
			continue;
		}

		// 10. Square bracket close ]
		if (ch === "]") {
			if (stack.length > 0 && stack[stack.length - 1].isBracket === true) {
				const top = stack.pop()!;
				if (top.cmd === "\\def") {
					const closedBracket = text.slice(top.openIndex + 1, i).trim();
					const argcVal = parseInt(closedBracket, 10);
					if (!isNaN(argcVal) && top.macroName) {
						userMacroArgc[top.macroName] = argcVal;
					}
					pendingTarget = { cmd: "\\def", stage: "body", cmdStart: top.cmdStart, argIndex: 1, macroName: top.macroName };
				} else if (top.cmd === "\\wait") {
					pendingTarget = { cmd: "\\wait", stage: "slot", cmdStart: top.cmdStart, argIndex: 0 };
				} else if (top.cmd === "\\guarded") {
					pendingTarget = { cmd: "\\guarded", stage: "pred", cmdStart: top.cmdStart, argIndex: 1, hasThrottle: true };
				}

				const req = getNextRequiredBlock(top.cmd, top.argIndex, top.isBracket, top.hasThrottle);
				if (req) {
					const gapStart = i + 1;
					const nextNonWs = skipWhitespaceAndComments(text, gapStart);
					const nextCh = nextNonWs < len ? text[nextNonWs] : null;
					const isPresent = nextCh === "{";

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

				i++;
				continue;
			}
			i++;
			continue;
		}

		// 11. Curly brace open {
		if (ch === "{") {
			const parent = stack.length > 0 ? stack[stack.length - 1] : null;
			let cmd = "";
			let cmdStart = -1;
			let argIndex = 0;
			let context = "Block";
			let macroName = "";
			let repCount = "";
			let callTarget = "";
			let hasThrottle = false;

			if (pendingTarget) {
				cmd = pendingTarget.cmd;
				cmdStart = pendingTarget.cmdStart;
				argIndex = pendingTarget.argIndex;
				macroName = pendingTarget.macroName || "";
				repCount = pendingTarget.repCount || "";
				callTarget = pendingTarget.callTarget || "";
				hasThrottle = !!pendingTarget.hasThrottle;

				const simpleCtx: Record<string, string> = {
					"\\all": "\\all (Conjunction)",
					"\\any": "\\any (Disjunction)",
					"\\move": "\\move > Arg Pack {dx, dy}",
					"\\row": "\\row > Pixel Pack",
					"\\rgb": "\\rgb > Arg Pack {r, g, b}",
					"\\typenum": "\\typenum (ref)",
					"\\wait": "\\wait (slot)",
					"\\use": "\\use (ns)"
				};
				if (cmd === "\\if") {
					context = argIndex === 0 ? "\\if (Condition)" : (argIndex === 1 ? "\\if (Then-branch)" : "\\if (Else-branch)");
				} else if (cmd === "\\guarded") {
					const eff = hasThrottle ? argIndex - 1 : argIndex;
					context = eff === 0 ? "\\guarded (Invariant)" : (eff === 1 ? "\\guarded (Body)" : "\\guarded (Abort-handler)");
				} else if (cmd === "\\rep" || cmd === "\\def") {
					context = `${cmd} (${argIndex === 0 ? (cmd === "\\rep" ? "count" : "name") : "Body"})`;
				} else if (cmd === "\\set" || cmd === "\\add" || cmd === "\\sub") {
					context = `${cmd} (${argIndex === 0 ? "var" : "val"})`;
				} else if (cmd === "\\click" || cmd === "\\mdown" || cmd === "\\mup") {
					context = `${cmd} (btn)`;
				} else if (cmd === "\\kdown" || cmd === "\\kup" || cmd === "\\tap") {
					context = `${cmd} (key)`;
				} else if (cmd === "\\delay" || cmd === "\\pace") {
					context = `${cmd} (duration)`;
				} else if (cmd === "\\pixelrect") {
					context = `\\pixelrect (${argIndex === 0 ? "var" : "rows"})`;
				} else if (cmd === "\\call") {
					context = argIndex === 0 ? "\\call (target)" : "\\call > Arg Pack";
				} else {
					context = simpleCtx[cmd] || cmd;
				}
				pendingTarget = null;
			} else {
				if (isCondScope(parent)) {
					context = "Predicate Triplet";
				} else if (parent && (parent.cmd === "\\call" || parent.cmd === "\\pixelrect" || parent.cmd === "\\move")) {
					context = "Arg Pack";
				} else {
					context = "Block";
				}
			}

			stack.push({
				char: "{",
				line: currentLine,
				openIndex: i,
				cmd,
				cmdStart,
				argIndex,
				context,
				isBracket: false,
				macroName,
				repCount,
				callTarget,
				hasThrottle
			});
			i++;
			continue;
		}

		// 12. Curly brace close }
		if (ch === "}") {
			if (stack.length > 0) {
				const top = stack[stack.length - 1];
				if (top.isBracket) {
					if (!firstUnmatchedClose) {
						firstUnmatchedClose = { char: "}", line: currentLine };
					}
				} else {
					stack.pop();

					const closedContent = text.slice(top.openIndex + 1, i).trim();
					if (top.cmd === "\\def" && top.argIndex === 0) {
						top.macroName = closedContent;
						if (closedContent) userMacros.add(closedContent);
					} else if (top.cmd === "\\rep" && top.argIndex === 0) {
						top.repCount = closedContent;
					} else if (top.cmd === "\\call" && top.argIndex === 0) {
						top.callTarget = closedContent;
					} else if (top.cmd === "\\pixelrect" && top.argIndex === 0) {
						top.macroName = closedContent;
						if (closedContent) userVars.add(closedContent);
					} else if ((top.cmd === "\\set" || top.cmd === "\\add" || top.cmd === "\\sub") && top.argIndex === 0) {
						if (closedContent) userVars.add(closedContent);
					}

					if (top.cmd === "\\def") {
						if (top.argIndex === 0) {
							pendingTarget = { cmd: "\\def", stage: "argc_or_body", cmdStart: top.cmdStart, argIndex: 1, macroName: top.macroName };
						}
					} else if (top.cmd === "\\if") {
						if (top.argIndex === 0) {
							pendingTarget = { cmd: "\\if", stage: "then", cmdStart: top.cmdStart, argIndex: 1 };
						} else if (top.argIndex === 1) {
							pendingTarget = { cmd: "\\if", stage: "else_or_done", cmdStart: top.cmdStart, argIndex: 2 };
						}
					} else if (top.cmd === "\\guarded") {
						const eff = top.hasThrottle ? top.argIndex - 1 : top.argIndex;
						if (eff === 0) {
							pendingTarget = { cmd: "\\guarded", stage: "body", cmdStart: top.cmdStart, argIndex: top.argIndex + 1, hasThrottle: top.hasThrottle };
						} else if (eff === 1) {
							pendingTarget = { cmd: "\\guarded", stage: "abort_or_done", cmdStart: top.cmdStart, argIndex: top.argIndex + 1, hasThrottle: top.hasThrottle };
						}
					} else if (top.cmd === "\\rep") {
						if (top.argIndex === 0) {
							pendingTarget = { cmd: "\\rep", stage: "body", cmdStart: top.cmdStart, argIndex: 1, repCount: top.repCount };
						}
					} else if (top.cmd === "\\set" || top.cmd === "\\add" || top.cmd === "\\sub") {
						if (top.argIndex === 0) {
							pendingTarget = { cmd: top.cmd, stage: "val", cmdStart: top.cmdStart, argIndex: 1 };
						}
					} else if (top.cmd === "\\pixelrect") {
						if (top.argIndex === 0) {
							pendingTarget = { cmd: "\\pixelrect", stage: "rows", cmdStart: top.cmdStart, argIndex: 1, macroName: top.macroName };
						}
					} else if (top.cmd === "\\call") {
						const totalArgs = CALL_ARG_COUNTS[top.callTarget || ""] ?? (top.callTarget ? 1 : 0);
						if (top.argIndex < totalArgs) {
							pendingTarget = {
								cmd: "\\call",
								stage: "args",
								cmdStart: top.cmdStart,
								argIndex: top.argIndex + 1,
								callTarget: top.callTarget,
								expectedArgs: totalArgs
							};
						}
					} else if (top.cmd && userMacros.has(top.cmd.slice(1))) {
						const macroName = top.cmd.slice(1);
						const totalArgs = userMacroArgc[macroName] || 0;
						if (top.argIndex + 1 < totalArgs) {
							pendingTarget = {
								cmd: top.cmd,
								stage: "args",
								cmdStart: top.cmdStart,
								argIndex: top.argIndex + 1,
								expectedArgs: totalArgs
							};
						}
					}

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
				}
			} else if (!firstUnmatchedClose) {
				firstUnmatchedClose = { char: "}", line: currentLine };
			}
			i++;
			continue;
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
		firstIncompleteStatement,
		userMacros,
		userVars
	};
}

/**
 * Checks whole-document bracket balance ({}, []) and returns active depth at cursor.
 */
export function checkBracketBalance(text: string, cursor?: number): BracketCheckResult {
	const scan = scanBraces(text, cursor);

	if (scan.syntaxError) {
		return {
			balanced: false,
			depth: scan.depthAtCursor,
			statusText: scan.syntaxError.msg,
			statusType: "error",
			unclosedLine: scan.syntaxError.line
		};
	}

	if (scan.unclosedVerbatim) {
		return {
			balanced: false,
			depth: scan.depthAtCursor,
			statusText: `⚠️ Unclosed verbatim block (\\text{}) at line ${scan.unclosedVerbatim.line}`,
			statusType: "warning",
			unclosedLine: scan.unclosedVerbatim.line
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

	if (scan.firstIncompleteStatement) {
		const inc = scan.firstIncompleteStatement;
		return {
			balanced: false,
			depth: scan.depthAtCursor,
			statusText: `⚠️ Incomplete statement: ${inc.cmd} requires {${inc.missing}}`,
			statusType: "warning",
			unclosedLine: inc.line
		};
	}

	const unclosedFrames = scan.stackAtEnd;
	if (unclosedFrames.length > 0) {
		const root = unclosedFrames[0];
		const leaf = unclosedFrames[unclosedFrames.length - 1];
		const rootChar = root.char;
		const rootContext = root.context || (root.isBracket ? "decorator" : "block");
		const suffix = unclosedFrames.length > 1 ? ` (${unclosedFrames.length} unclosed)` : "";
		const statusText = `⚠️ Unclosed '${rootChar}' for ${rootContext} at line ${root.line}${suffix}`;

		return {
			balanced: false,
			depth: scan.depthAtCursor,
			statusText,
			statusType: "warning",
			unclosedChar: leaf.char,
			unclosedLine: leaf.line,
			firstUnclosedChar: rootChar,
			firstUnclosedLine: root.line,
			firstUnclosedContext: rootContext,
			unclosedCount: unclosedFrames.length
		};
	}

	return {
		balanced: true,
		depth: scan.depthAtCursor,
		statusText: `✓ Braces: Balanced (depth ${scan.depthAtCursor})`,
		statusType: "balanced"
	};
}

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
			"img.at": "Arg Pack {snap}{x, y}",
			"img.diff": "Arg Pack {snap_a}{snap_b}{tol}",
			"img.watch": "Arg Pack {name}{x, y}{interval, duration}",
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

const BASE_VARS: [string, CandidateKind, string][] = [
	["ret", "var", "System return value / Handle ID of latest \\call"],
	["err", "var", "System error code (0 = ERR_OK, 1..8 errors)"],
	["timeout", "var", "Wait timeout flag (0 = arrived, 1 = timeout)"],
	["slot.net.msg.val", "slot", "Network message arrival receptor slot"],
	["slot.img.btn.val", "slot", "Vision stream arrival receptor slot"]
];

function getAllVariableCandidates(isDelimited = false, userVars?: Set<string>): InspectorCandidate[] {
	const systemCandidates: InspectorCandidate[] = BASE_VARS.map(([name, kind, desc]) => ({
		label: `$${name}`,
		insert: isDelimited ? `\${${name}}` : `$${name}`,
		kind,
		desc
	}));

	const userVarCandidates: InspectorCandidate[] = userVars
		? Array.from(userVars).map(v => ({
			label: `$${v}`,
			insert: isDelimited ? `\${${v}}` : `$${v}`,
			kind: "var",
			desc: `User variable '${v}' (general register)`
		}))
		: [];

	return [...systemCandidates, ...userVarCandidates];
}

/**
 * Universal token slice and replacement boundary resolver.
 * Eliminates repetitive slice, whitespace, and offset boilerplate.
 */
function sliceTokenRange(
	text: string,
	openPos: number,
	cursor: number,
	delimiter?: string
): { prefix: string; raw: string; tokenStart: number; tokenEnd: number } {
	const content = text.slice(openPos + 1, cursor);
	const delimIdx = delimiter ? content.lastIndexOf(delimiter) : -1;
	const segment = delimIdx !== -1 ? content.slice(delimIdx + 1) : content;
	const leading = segment.length - segment.trimStart().length;
	const raw = segment.trim();
	const prefix = raw.toLowerCase().replace(/^\\/, "");
	const base = openPos + 1 + (delimIdx !== -1 ? delimIdx + 1 : 0);
	return {
		prefix,
		raw,
		tokenStart: base + leading,
		tokenEnd: cursor
	};
}

export function detectToken(text: string, cursor: number): DetectionResult | null {
	const lineStart = text.lastIndexOf("\n", cursor - 1) + 1;
	const lineBefore = text.slice(lineStart, cursor);

	// 0. Inside verbatim text blocks (\text{}...\endtext{}), only \endtext is active
	if (isInsideVerbatim(text, cursor)) {
		const endtextMatch = lineBefore.match(/(?:^|[^\\])(\\[a-zA-Z0-9_]*)$/);
		if (endtextMatch) {
			const rawToken = endtextMatch[1];
			const tokenStart = cursor - rawToken.length;
			const cand = COMMAND_CANDIDATES.find(c => c.label === "\\endtext");
			const matches = (cand && "\\endtext".startsWith(rawToken)) ? [cand] : [];
			return { context: "Verbatim Block Terminator", rawToken, tokenStart, tokenEnd: cursor, matches };
		}
		return null;
	}

	// 1. Special Escapes: \\, \{, \}, \$
	const escMatch = lineBefore.match(/\\[\\{}$]$/);
	if (escMatch) {
		const rawToken = escMatch[0];
		const cand = ESCAPE_CANDIDATES.find(c => c.label === rawToken);
		return { context: "Escape Sequence", rawToken, tokenStart: cursor - 2, tokenEnd: cursor, matches: cand ? [cand] : [] };
	}

	// 2. Syntax Directives starting with \#!
	const directiveMatch = lineBefore.match(/\\#!([^\r\n]*)$/);
	if (directiveMatch) {
		const rawToken = directiveMatch[0];
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
		return { context: "Syntax Directive", rawToken, tokenStart: cursor - rawToken.length, tokenEnd: cursor, matches };
	}

	// 3. Comment or Preamble Prefix \#
	if (lineBefore.endsWith("\\#")) {
		const commentCandidate: InspectorCandidate = {
			label: "\\#",
			insert: "\\# ",
			kind: "escape",
			desc: "Single-line comment (or literal hash)"
		};
		return {
			context: "Comment / Directive",
			rawToken: "\\#",
			tokenStart: cursor - 2,
			tokenEnd: cursor,
			matches: [commentCandidate, ...SYNTAX_CANDIDATES]
		};
	}

	// Execute single-pass DFA to retrieve active scope, user macros, and user variables
	const scan = scanBraces(text, cursor);

	// 4. Variable / System Register / Slot ($ or ${)
	const varMatch = lineBefore.match(/(?:\$|\$\{)([a-zA-Z0-9_.]*)$/);
	if (varMatch) {
		const isDelimited = varMatch[0].startsWith("${");
		const prefix = varMatch[1].toLowerCase();
		const allVars = getAllVariableCandidates(isDelimited, scan.userVars);
		const matches = allVars.filter(v => {
			if (!prefix) return true;
			const clean = v.label.replace(/^\$/, "").toLowerCase();
			return clean.startsWith(prefix) || clean.includes("." + prefix);
		});
		return { context: "Variable / Slot", rawToken: varMatch[0], tokenStart: cursor - varMatch[0].length, tokenEnd: cursor, matches };
	}

	// 5. Context: Inside curly braces {...}
	const stack = scan.cursorStack;
	if (stack.length > 0) {
		const current = stack[stack.length - 1];
		const parent = stack.length >= 2 ? stack[stack.length - 2] : null;

		// 5a. Mouse click / holds: \click, \mdown, \mup
		if ((current.cmd === "\\click" || current.cmd === "\\mdown" || current.cmd === "\\mup") && current.argIndex === 0) {
			const content = text.slice(current.openIndex + 1, cursor);
			const commaIdx = content.indexOf(",");
			if (commaIdx === -1) {
				const { prefix, raw, tokenStart, tokenEnd } = sliceTokenRange(text, current.openIndex, cursor);
				const matches = MOUSE_BUTTON_CANDIDATES.filter(b => !prefix || b.label.toLowerCase().startsWith(prefix));
				return { context: "Mouse Button", rawToken: raw || "{btn}", tokenStart, tokenEnd, matches };
			}
			const { raw, tokenStart, tokenEnd } = sliceTokenRange(text, current.openIndex + commaIdx + 1, cursor);
			return { context: "Click Count", rawToken: raw || "{count}", tokenStart, tokenEnd, matches: [] };
		}

		// 5b. Key holds & taps: \kdown, \kup, \tap
		if (current.cmd === "\\kdown" || current.cmd === "\\kup" || current.cmd === "\\tap") {
			const { prefix, raw, tokenStart, tokenEnd } = sliceTokenRange(text, current.openIndex, cursor, "+");
			const matches = KEY_NAME_CANDIDATES.filter(k => !prefix || k.label.toLowerCase().startsWith(prefix));
			return { context: "Key / Modifier", rawToken: raw || "{key}", tokenStart, tokenEnd, matches };
		}

		// 5c. Namespace: \use
		if (current.cmd === "\\use" && current.argIndex === 0) {
			const { prefix, raw, tokenStart, tokenEnd } = sliceTokenRange(text, current.openIndex, cursor);
			const matches = NAMESPACE_CANDIDATES.filter(n => !prefix || n.label.toLowerCase().startsWith(prefix));
			return { context: "Namespace", rawToken: raw || "{ns}", tokenStart, tokenEnd, matches };
		}

		// 5d. Extension call: \call
		if (current.cmd === "\\call" && current.argIndex === 0) {
			const { prefix, raw, tokenStart, tokenEnd } = sliceTokenRange(text, current.openIndex, cursor);
			const matches = EXT_FUNCTION_CANDIDATES.filter(f => !prefix || f.label.toLowerCase().startsWith(prefix));
			return { context: "Extension Call", rawToken: raw || "{ns.fn}", tokenStart, tokenEnd, matches };
		}

		// 5e. Predicate Triplet: {lhs, op, rhs} in \if, \guarded, \all, \any
		const isTriplet = current.context === "Predicate Triplet" || Boolean(parent && isCondScope(parent) && !current.cmd);

		if (isTriplet) {
			const tripletContent = text.slice(current.openIndex + 1, cursor);
			const parts = tripletContent.split(",");

			if (parts.length === 1 || parts.length === 3) {
				const isLhs = parts.length === 1;
				const startPos = isLhs ? current.openIndex : current.openIndex + 1 + parts[0].length + 1 + parts[1].length;
				const { prefix, raw, tokenStart, tokenEnd } = sliceTokenRange(text, startPos, cursor);
				const allVars = getAllVariableCandidates(false, scan.userVars);
				const matches = allVars.filter(v => {
					if (!prefix) return true;
					const clean = v.label.replace(/^\$/, "").toLowerCase();
					return clean.startsWith(prefix) || v.label.toLowerCase().startsWith(prefix);
				});
				return { context: "Predicate Operand", rawToken: raw || (isLhs ? "{lhs}" : "{rhs}"), tokenStart, tokenEnd, matches };
			}

			if (parts.length === 2) {
				const firstCommaPos = current.openIndex + 1 + parts[0].length;
				const { prefix, raw, tokenStart, tokenEnd } = sliceTokenRange(text, firstCommaPos, cursor);
				const matches = PREDICATE_OP_CANDIDATES.filter(o => !prefix || o.label.startsWith(prefix));
				return { context: "Predicate Operator", rawToken: raw || "{op}", tokenStart, tokenEnd, matches };
			}
		}

		// 5f. Condition block directly under \if, \guarded, \all, \any
		if (isCondScope(current)) {
			const cmdMatch = lineBefore.match(/\\([a-zA-Z0-9_]*)$/);
			if (cmdMatch) {
				const rawToken = cmdMatch[0];
				const prefix = cmdMatch[1].toLowerCase();
				const tokenStart = cursor - rawToken.length;
				const matches = PREDICATE_LOGIC_CANDIDATES.filter(c =>
					!prefix || c.label.toLowerCase().startsWith(rawToken.toLowerCase()) || c.label.replace(/^\\/, "").toLowerCase().startsWith(prefix)
				);
				return { context: "Predicate Condition", rawToken, tokenStart, tokenEnd: cursor, matches };
			}

			const { prefix, raw, tokenStart, tokenEnd } = sliceTokenRange(text, current.openIndex, cursor);
			const tripletTemplate: InspectorCandidate = {
				label: "{}",
				insert: "{ $ret, =, 0 }",
				kind: "op",
				desc: "Comparison triplet {lhs, op, rhs}"
			};
			const pool = (current.cmd === "\\all" || current.cmd === "\\any") ? [tripletTemplate] : [...PREDICATE_LOGIC_CANDIDATES, tripletTemplate];
			const matches = pool.filter(c => !prefix || c.label.toLowerCase().startsWith(prefix) || c.label.replace(/^\\/, "").toLowerCase().startsWith(prefix));
			return { context: "Predicate Condition", rawToken: raw || "{cond}", tokenStart, tokenEnd, matches };
		}

		// 5g. Argument Packs: \move, \rgb, \call
		if (current.cmd === "\\move") {
			const parts = text.slice(current.openIndex + 1, cursor).split(",");
			const isDx = parts.length === 1;
			const raw = parts[parts.length - 1].trim();
			return { context: isDx ? "Move X (dx)" : "Move Y (dy)", rawToken: raw || (isDx ? "{dx}" : "{dy}"), tokenStart: current.openIndex + 1, tokenEnd: cursor, matches: [] };
		}

		if (current.cmd === "\\rgb") {
			const parts = text.slice(current.openIndex + 1, cursor).split(",");
			const idx = parts.length;
			const label = idx === 1 ? "RGB Red" : (idx === 2 ? "RGB Green" : "RGB Blue");
			const raw = parts[idx - 1].trim();
			return { context: label, rawToken: raw || (idx === 1 ? "{r}" : (idx === 2 ? "{g}" : "{b}")), tokenStart: current.openIndex + 1, tokenEnd: cursor, matches: [] };
		}

		if (current.cmd === "\\call" && current.argIndex >= 1) {
			const raw = text.slice(current.openIndex + 1, cursor).trim();
			return { context: `Call Args (${current.callTarget || "extension"})`, rawToken: raw || "{args}", tokenStart: current.openIndex + 1, tokenEnd: cursor, matches: [] };
		}
	}

	// 6. General Command / Key / Macro / Combo starting with \
	const cmdMatch = lineBefore.match(/\\([a-zA-Z0-9_+]*)$/);
	if (cmdMatch) {
		const rawToken = cmdMatch[0];
		const prefix = cmdMatch[1].toLowerCase();
		const tokenStart = cursor - rawToken.length;
		const tokenEnd = cursor;

		const userMacroCandidates: InspectorCandidate[] = Array.from(scan.userMacros).map(name => ({
			label: `\\${name}`,
			insert: `\\${name}{}`,
			kind: "macro",
			desc: `User macro '${name}'`
		}));

		if (prefix === "") {
			const allRoot = [
				...COMMAND_CANDIDATES,
				...SYNTAX_CANDIDATES.slice(0, 2),
				...ESCAPE_CANDIDATES,
				...userMacroCandidates
			];
			return { context: "Command / Key", rawToken: "\\", tokenStart, tokenEnd, matches: allRoot };
		}

		const allCmds = [...COMMAND_CANDIDATES, ...userMacroCandidates];
		const matches = allCmds.filter(c => {
			const clean = c.label.replace(/^\\/, "").toLowerCase();
			return clean.startsWith(prefix) || c.label.toLowerCase().startsWith(`\\${prefix}`);
		});

		return { context: "Command / Key", rawToken, tokenStart, tokenEnd, matches };
	}

	// 7. Transition Scope Completion: awaiting subsequent mandatory block
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
